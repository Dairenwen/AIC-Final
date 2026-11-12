import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import markdownItKatex from 'markdown-it-katex'

import 'katex/dist/katex.min.css'

const markdown = new MarkdownIt({
  breaks: false,
  html: false,
  linkify: true,
  typographer: true,
}).use(markdownItKatex)

/** Render model-authored Markdown without allowing it to inject executable HTML. */
export function renderMarkdown(source: string): string {
  if (!source.trim()) return ''
  return DOMPurify.sanitize(markdown.render(source), {
    ADD_ATTR: ['target', 'rel'],
    USE_PROFILES: { html: true },
  })
}
