from io import BytesIO
from pathlib import Path
from uuid import uuid4
import warnings

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import PropertyPhoto

FORMATS = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP'}


def prepare_photos(files):
    """Validate the entire batch before changing the DB or writing any file."""
    prepared = []
    for upload in files or []:
        if not upload.filename:
            continue
        if len(prepared) >= 12:
            raise ValueError('Не более 12 фотографий за одну загрузку.')
        extension = secure_filename(upload.filename).rsplit('.', 1)[-1].lower()
        if extension not in FORMATS:
            raise ValueError('Разрешены только JPEG, PNG и WebP.')
        content = upload.stream.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise ValueError('Фотография превышает 5 МБ.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as photo:
                    if photo.format != FORMATS[extension]:
                        raise ValueError('Содержимое файла не соответствует расширению.')
                    if photo.width * photo.height > 20_000_000:
                        raise ValueError('Изображение слишком большое (максимум 20 Мп).')
                    photo.load()
                    buffer = BytesIO()
                    photo.convert('RGB').save(buffer, 'JPEG', quality=90)  # Strip metadata and re-encode.
                    prepared.append((uuid4().hex + '.jpg', buffer.getvalue()))
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError('Файл не является безопасным изображением.') from error
    return prepared


def attach_photos(property, prepared):
    written = []
    folder = Path(current_app.config['UPLOAD_FOLDER'])
    folder.mkdir(parents=True, exist_ok=True)
    for filename, data in prepared:
        path = folder / filename
        written.append(path)
        try:
            path.write_bytes(data)
        except OSError:
            for saved in written:
                saved.unlink(missing_ok=True)
            raise
        db.session.add(PropertyPhoto(property=property, image_url='uploads/properties/' + filename,
                                     is_main=not property.photos, sort_order=len(property.photos)))
    return written


def photo_path(photo):
    root = Path(current_app.config['UPLOAD_FOLDER']).resolve()
    filename = Path(photo.image_url).name
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError('Недопустимый путь фотографии.')
    return path
