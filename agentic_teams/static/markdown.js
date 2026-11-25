"use strict";
// Small, safe Markdown for chat messages (no dependencies).
// All text is escaped BEFORE it becomes HTML; only the markup below produces tags:
// ``` code blocks, `code`, **bold**, *italic*, ~~strikethrough~~, [link](http…), bare URLs,
// # headings, lists (- * + and 1. 1)), > quotes, | tables, --- rule.
// Single line breaks become <br> (in chat, every Enter counts).
(function (root) {
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ESC[c]);

  function safeUrl(u) {
    const raw = u.replace(/&amp;/g, "&");
    return /^(https?:\/\/|mailto:)/i.test(raw) ? u : null;
  }

  // Inline markup over raw text; `decorate` runs on the plain-text pieces (e.g. @mentions).
  function inline(text, decorate) {
    const keep = [];
    const hold = html => `\u0000${keep.push(html) - 1}\u0000`;
    let s = esc(text);
    s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (m, ticks, code) => hold(`<code>${code.trim() === "" ? code : code.replace(/^ (.*) $/, "$1")}</code>`));
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const u = safeUrl(url);
      return u ? hold(`<a href="${u}" target="_blank" rel="noopener noreferrer">${label}</a>`) : m;
    });
    s = s.replace(/\bhttps?:\/\/[^\s<]*[^\s<.,;:!?)\]'"]/g, url => hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`));
    s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, "$1<strong>$2</strong>");
    s = s.replace(/(^|[^\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
    if (decorate) s = s.split(/(\u0000\d+\u0000|<[^>]+>)/).map((part, i) => (i % 2 ? part : decorate(part))).join("");
    return s.replace(/\u0000(\d+)\u0000/g, (m, i) => keep[+i]);
  }

  const RE = {
    fence: /^\s*(```+|~~~+)\s*([\w+#.-]*)\s*$/,
    heading: /^(#{1,6})\s+(.*?)\s*#*\s*$/,
    hr: /^\s*([-*_])(\s*\1){2,}\s*$/,
    quote: /^\s*>\s?(.*)$/,
    item: /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/,
    tableSep: /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/,
  };

  const cells = line => line.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());

  function blocks(lines, decorate) {
    const out = [];
    let para = [];
    const flush = () => {
      if (para.length) out.push(`<p>${para.map(l => inline(l, decorate)).join("<br>")}</p>`);
      para = [];
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let m;
      if ((m = line.match(RE.fence))) {
        flush();
        const code = [];
        const close = new RegExp("^\\s*" + m[1][0] + "{" + m[1].length + ",}\\s*$");
        for (i++; i < lines.length && !close.test(lines[i]); i++) code.push(lines[i]);
        const lang = m[2] ? ` class="lang-${esc(m[2])}"` : "";
        out.push(`<pre><code${lang}>${esc(code.join("\n"))}</code></pre>`);
        continue;
      }
      if (!line.trim()) { flush(); continue; }
      if ((m = line.match(RE.heading))) {
        flush();
        const n = Math.min(m[1].length + 2, 6);
        out.push(`<h${n}>${inline(m[2], decorate)}</h${n}>`);
        continue;
      }
      if (RE.hr.test(line) && !para.length) { out.push("<hr>"); continue; }
      if (RE.quote.test(line)) {
        flush();
        const q = [];
        for (; i < lines.length && (m = lines[i].match(RE.quote)); i++) q.push(m[1]);
        i--;
        out.push(`<blockquote>${blocks(q, decorate)}</blockquote>`);
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && RE.tableSep.test(lines[i + 1])) {
        flush();
        const head = cells(line);
        const rows = [];
        for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(cells(lines[i]));
        i--;
        out.push("<table><thead><tr>" + head.map(c => `<th>${inline(c, decorate)}</th>`).join("") +
                 "</tr></thead><tbody>" + rows.map(r => "<tr>" + head.map((_, k) => `<td>${inline(r[k] || "", decorate)}</td>`).join("") + "</tr>").join("") +
                 "</tbody></table>");
        continue;
      }
      if ((m = line.match(RE.item))) {
        flush();
        const base = m[1].length;
        const ordered = /\d/.test(m[2]);
        const start = ordered ? parseInt(m[2], 10) : 1;
        const items = [];
        for (; i < lines.length; i++) {
          const l = lines[i];
          const im = l.match(RE.item);
          if (im && im[1].length <= base + 1 && /\d/.test(im[2]) === ordered) { items.push([im[3]]); continue; }
          if (!l.trim()) {  // blank line: the list goes on only if an item or indented text follows
            const next = lines[i + 1] || "";
            const nm = next.match(RE.item);
            if ((nm && nm[1].length <= base + 1 && /\d/.test(nm[2]) === ordered) || /^\s{2,}\S/.test(next)) continue;
            break;
          }
          if (/^\s+\S/.test(l) && items.length) { items[items.length - 1].push(l.replace(/^\s{1,4}/, "")); continue; }
          break;
        }
        i--;
        const tag = ordered ? "ol" : "ul";
        const attr = ordered && start !== 1 ? ` start="${start}"` : "";
        out.push(`<${tag}${attr}>` + items.map(it => {
          const inner = blocks(it, decorate);
          return `<li>${it.length === 1 ? inner.replace(/^<p>([\s\S]*)<\/p>$/, "$1") : inner}</li>`;
        }).join("") + `</${tag}>`);
        continue;
      }
      para.push(line);
    }
    flush();
    return out.join("");
  }

  function renderMarkdown(text, decorate) {
    return blocks(String(text ?? "").replace(/\r\n?/g, "\n").split("\n"), decorate);
  }

  root.renderMarkdown = renderMarkdown;
  if (typeof module !== "undefined") module.exports = { renderMarkdown };
})(typeof window !== "undefined" ? window : globalThis);
