/**
 * src/components/Markdown.tsx
 *
 * Purpose:        Render the LLM's Markdown answer as styled React elements —
 *                 headings, bold, inline code, fenced code blocks (with light
 *                 Java/SQL highlighting), bullet + numbered lists, blockquotes,
 *                 rules — plus CodeXray's FACT / INFERENCE / UNKNOWN label
 *                 colouring (build plan §50).
 * Responsibility: A small, dependency-free block + inline parser. Output is real
 *                 elements (never `dangerouslySetInnerHTML`), so there is no XSS
 *                 surface even though the text comes from a model.
 */
import { Fragment, type ReactNode } from "react";

// --------------------------------------------------------------- inline parsing
const LABEL_RE = /^(\s*(?:[-*>]\s*)?(?:\*\*|__)?\s*)(FACT|INFERENCE|UNKNOWN)((?:\*\*|__)?\s*:)/i;

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  // split on **bold** and `code`
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={`${keyBase}-b${i}`}>{tok.slice(2, -2)}</strong>);
    else out.push(<code key={`${keyBase}-c${i}`} className="md-icode">{tok.slice(1, -1)}</code>);
    last = m.index + tok.length;
    i++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ------------------------------------------------------------- code highlighting
const JAVA_KW = new Set(
  ("abstract assert boolean break byte case catch char class const continue default do double else enum " +
    "extends final finally float for goto if implements import instanceof int interface long native new package " +
    "private protected public return short static strictfp super switch synchronized this throw throws transient " +
    "try void volatile while var record true false null").split(" "),
);
const SQL_KW = new Set(
  ("select from where join inner left right outer on group by order having as and or not null insert into values " +
    "update set delete merge union all distinct case when then end limit").split(" "),
);

function highlight(code: string, lang: string, keyBase: string): ReactNode[] {
  const kw = /sql/i.test(lang) ? SQL_KW : JAVA_KW;
  const token = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b\d[\d_.]*\b|[A-Za-z_]\w*)/g;
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = token.exec(code))) {
    if (m.index > last) out.push(code.slice(last, m.index));
    const t = m[0];
    let cls = "";
    if (t.startsWith("//") || t.startsWith("/*")) cls = "tok-com";
    else if (t.startsWith('"') || t.startsWith("'")) cls = "tok-str";
    else if (/^\d/.test(t)) cls = "tok-num";
    else if (kw.has(t.toLowerCase()) && (kw === JAVA_KW ? kw.has(t) : true)) cls = "tok-kw";
    out.push(cls ? <span key={`${keyBase}-t${i}`} className={cls}>{t}</span> : t);
    last = m.index + t.length;
    i++;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

// --------------------------------------------------------------- block parsing
export function Markdown({ text }: { text: string }) {
  const lines = (text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;

  const flushPara = (buf: string[]) => {
    if (!buf.length) return;
    const joined = buf.join(" ");
    const lbl = joined.match(LABEL_RE);
    if (lbl) {
      blocks.push(
        <p key={`p${k++}`} className="md-p">
          <span className={`label-${lbl[2].toUpperCase()}`}>{lbl[2].toUpperCase()}</span>
          {lbl[3].replace(/\*/g, "")}
          {inline(joined.slice(lbl[0].length), `p${k}`)}
        </p>,
      );
    } else {
      blocks.push(<p key={`p${k++}`} className="md-p">{inline(joined, `p${k}`)}</p>);
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    const fence = line.match(/^\s*```+\s*([\w-]*)/);
    if (fence) {
      const lang = fence[1] || "";
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```+\s*$/.test(lines[i])) body.push(lines[i++]);
      i++; // closing fence
      blocks.push(
        <pre key={`code${k++}`} className="md-code">
          {lang && <span className="md-code-lang">{lang}</span>}
          <code>{highlight(body.join("\n"), lang, `code${k}`)}</code>
        </pre>,
      );
      continue;
    }

    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = Math.min(h[1].length, 4);
      const Tag = (`h${level}` as "h1" | "h2" | "h3" | "h4");
      blocks.push(<Tag key={`h${k++}`} className="md-h">{inline(h[2], `h${k}`)}</Tag>);
      i++;
      continue;
    }

    // horizontal rule
    if (/^\s*([-*_])\1\1[-*_\s]*$/.test(line)) {
      blocks.push(<hr key={`hr${k++}`} className="md-hr" />);
      i++;
      continue;
    }

    // blockquote
    if (/^\s*>\s?/.test(line)) {
      const q: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={`bq${k++}`} className="md-bq">{inline(q.join(" "), `bq${k}`)}</blockquote>);
      continue;
    }

    // lists (bullet or numbered)
    const li = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
    if (li) {
      const ordered = /\d+\./.test(li[2]);
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const mm = lines[i].match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
        if (!mm) break;
        items.push(<li key={`li${k}-${items.length}`}>{inline(mm[3], `li${k}-${items.length}`)}</li>);
        i++;
      }
      blocks.push(
        ordered
          ? <ol key={`ol${k++}`} className="md-list">{items}</ol>
          : <ul key={`ul${k++}`} className="md-list">{items}</ul>,
      );
      continue;
    }

    // blank line -> paragraph break
    if (!line.trim()) {
      i++;
      continue;
    }

    // paragraph (collect until blank / block start)
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*```+/.test(lines[i]) &&
      !/^#{1,6}\s/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^(\s*)([-*]|\d+\.)\s+/.test(lines[i])
    ) {
      buf.push(lines[i++]);
    }
    flushPara(buf);
  }

  return <div className="md">{blocks.map((b, idx) => <Fragment key={idx}>{b}</Fragment>)}</div>;
}
