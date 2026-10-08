// Expects the indicator popup to be OPEN (harness opens it). Returns open state + all labels.
(() => { const i = Main.panel.statusArea['ai-usage']; if (!i) return 'NO INDICATOR';
const txt = []; const walk = a => { if (a.text !== undefined && a.text) txt.push(a.text); a.get_children().forEach(walk); };
walk(i.menu.box);
return JSON.stringify({ isOpen: i.menu.isOpen, items: i.menu.box.get_children().length,
  hasClaude: txt.indexOf('Claude Code') >= 0, hasCodex: txt.indexOf('Codex') >= 0,
  hasActualiser: txt.some(t => t.indexOf('Actualiser') >= 0), labels: txt }); })()
