(() => {
  'use strict';
  const preset = document.getElementById('analyticsPreset');
  const dates = ['analyticsStart', 'analyticsEnd'].map(id => document.getElementById(id));
  function updateDates() {
    dates.forEach(input => { if (input) { input.disabled = preset.value !== 'custom'; input.required = preset.value === 'custom'; } });
  }
  if (preset) { preset.addEventListener('change', updateDates); updateDates(); }
  const source = document.getElementById('analyticsData');
  if (!source) return;
  const charts = JSON.parse(source.textContent);
  const ns = 'http://www.w3.org/2000/svg';
  const palette = ['#2563eb', '#60a5fa', '#0f766e', '#94a3b8'];
  function node(type, attributes = {}, text = '') {
    const element = document.createElementNS(ns, type);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    if (text) element.textContent = text;
    return element;
  }
  function number(value) { return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1, notation: 'compact' }).format(value); }
  charts.forEach(chart => {
    const holder = document.querySelector(`[data-chart="${chart.key}"]`);
    if (!holder) return;
    if (!chart.values.length || !chart.values.some(value => value > 0)) {
      const empty = document.createElement('p');
      empty.className = 'analytics-empty';
      empty.textContent = 'Нет записей для построения графика. Значения равны нулю.';
      holder.append(empty);
      return;
    }
    const width = 640;
    const height = chart.kind === 'bar' ? Math.max(220, chart.labels.length * 49 + 25) : 280;
    const svg = node('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-labelledby': `svg-title-${chart.key} svg-desc-${chart.key}` });
    svg.append(node('title', { id: `svg-title-${chart.key}` }, chart.title));
    svg.append(node('desc', { id: `svg-desc-${chart.key}` }, chart.labels.map((label, i) => `${label}: ${chart.display_values[i]} ${chart.unit === '₽' ? '' : chart.unit}`).join('; ')));
    const maximum = Math.max(...chart.values);
    if (chart.kind === 'line') {
      const left = 65, top = 22, plotWidth = 550, plotHeight = 195;
      for (let tick = 0; tick <= 4; tick++) {
        const y = top + plotHeight - tick * plotHeight / 4;
        svg.append(node('line', { x1: left, y1: y, x2: left + plotWidth, y2: y, stroke: '#e5e7eb' }));
        svg.append(node('text', { x: left - 8, y: y + 4, 'text-anchor': 'end', 'font-size': 13, fill: '#64748b' }, number(maximum * tick / 4)));
      }
      const xAt = i => left + (chart.values.length === 1 ? plotWidth / 2 : i * plotWidth / (chart.values.length - 1));
      const yAt = value => top + plotHeight - value / maximum * plotHeight;
      svg.append(node('polyline', { points: chart.values.map((value, i) => `${xAt(i)},${yAt(value)}`).join(' '), fill: 'none', stroke: palette[0], 'stroke-width': 3 }));
      chart.values.forEach((value, i) => {
        const circle = node('circle', { cx: xAt(i), cy: yAt(value), r: 4, fill: palette[0] });
        circle.append(node('title', {}, `${chart.labels[i]}: ${chart.display_values[i]}`));
        svg.append(circle);
        if (i % Math.max(1, Math.ceil(chart.values.length / 6)) === 0 || i === chart.values.length - 1) {
          svg.append(node('text', { x: xAt(i), y: 245, 'text-anchor': 'middle', 'font-size': 13, fill: '#64748b' }, chart.labels[i]));
        }
      });
    } else if (chart.kind === 'donut') {
      const total = chart.values.reduce((sum, value) => sum + value, 0);
      const circumference = 2 * Math.PI * 80;
      let offset = 0;
      chart.values.forEach((value, i) => {
        const arc = node('circle', { cx: 145, cy: 137, r: 80, fill: 'none', stroke: palette[i % palette.length], 'stroke-width': 37,
          'stroke-dasharray': `${value / total * circumference} ${circumference}`, 'stroke-dashoffset': -offset, transform: 'rotate(-90 145 137)' });
        arc.append(node('title', {}, `${chart.labels[i]}: ${value}`));
        svg.append(arc);
        offset += value / total * circumference;
        svg.append(node('rect', { x: 280, y: 62 + i * 43, width: 15, height: 15, rx: 3, fill: palette[i % palette.length] }));
        svg.append(node('text', { x: 306, y: 75 + i * 43, 'font-size': 16, fill: '#334155' }, `${chart.labels[i]}: ${value}`));
      });
      svg.append(node('text', { x: 145, y: 145, 'text-anchor': 'middle', 'font-size': 28, fill: '#0f172a' }, String(total)));
    } else {
      chart.values.forEach((value, i) => {
        const y = 25 + i * 49;
        const label = chart.labels[i];
        svg.append(node('text', { x: 0, y, 'font-size': 14, fill: '#334155' }, label.length > 69 ? label.slice(0, 66) + '…' : label));
        const rect = node('rect', { x: 0, y: y + 9, width: value / maximum * 470, height: 13, rx: 4, fill: palette[i % palette.length] });
        rect.append(node('title', {}, `${label}: ${chart.display_values[i]}`));
        svg.append(rect);
        svg.append(node('text', { x: 485, y: y + 21, 'font-size': 14, fill: '#334155' }, number(value) + (chart.unit === '₽' ? ' ₽' : '')));
      });
    }
    holder.append(svg);
  });
})();
