import DOMPurify from 'dompurify'
import katex from 'katex'
import MarkdownIt from 'markdown-it'
import markdownItKatex from 'markdown-it-katex'

import 'katex/dist/katex.min.css'

const markdown = new MarkdownIt({
  breaks: false,
  html: false,
  linkify: true,
  typographer: true,
}).use(markdownItKatex)

// markdown-it-katex only tokenizes the math; it bundles a very old KaTeX whose
// markup does not match the current stylesheet (misplaced subscripts and
// limits). Render the tokens with the KaTeX version the stylesheet belongs to.
function renderMath(source: string, displayMode: boolean): string {
  try {
    return katex.renderToString(source, { displayMode, throwOnError: false, output: 'html', strict: 'ignore' })
  } catch {
    return markdown.utils.escapeHtml(source)
  }
}

markdown.renderer.rules.math_inline = (tokens, index) => renderMath(tokens[index].content, false)
markdown.renderer.rules.math_block = (tokens, index) => `<p class="katex-block">${renderMath(tokens[index].content, true)}</p>\n`

// Wide tables scroll inside their own box instead of squeezing every column.
markdown.renderer.rules.table_open = () => '<div class="md-table"><table>\n'
markdown.renderer.rules.table_close = () => '</table></div>\n'

/** Markdown to HTML before sanitizing; exported for tests, not for direct use in templates. */
export function markdownToHtml(source: string): string {
  return restoreCjkEmphasis(markdown.render(source))
}

/** Render model-authored Markdown without allowing it to inject executable HTML. */
export function renderMarkdown(source: string): string {
  if (!source.trim()) return ''
  return DOMPurify.sanitize(markdownToHtml(source), {
    ADD_ATTR: ['target', 'rel'],
    USE_PROFILES: { html: true },
  })
}

/**
 * CommonMark does not treat `**` next to full-width punctuation as emphasis
 * ("**术语（缩写）**是…"), so models writing Chinese leave literal asterisks
 * behind. Convert the leftovers to <strong>, leaving code untouched.
 */
export function restoreCjkEmphasis(html: string): string {
  return html
    .split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>|<[^>]+>)/g)
    .map((part) => (part.startsWith('<') ? part : part.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')))
    .join('')
}
