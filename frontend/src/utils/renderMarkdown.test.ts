import { describe, expect, it } from 'vitest'

import { markdownToHtml, normalizeMathDelimiters, restoreCjkEmphasis } from './renderMarkdown'

describe('markdownToHtml', () => {
  it('wraps tables so wide tables scroll instead of squeezing columns', () => {
    const html = markdownToHtml('| 维度 | 说明 |\n| --- | --- |\n| 召回 | 高 |')

    expect(html).toContain('<div class="md-table"><table>')
    expect(html).toContain('</table></div>')
  })

  it('renders bold text that ends with full-width punctuation', () => {
    const html = markdownToHtml('**RRF（倒数排名融合）**是一种融合方法。')

    expect(html).toContain('<strong>RRF（倒数排名融合）</strong>是一种融合方法。')
    expect(html).not.toContain('**')
  })

  it('leaves asterisks inside code untouched', () => {
    expect(restoreCjkEmphasis('<p>a <code>**kwargs**</code> b</p>')).toBe('<p>a <code>**kwargs**</code> b</p>')
    expect(restoreCjkEmphasis('<pre><code>x = **y**</code></pre>')).toBe('<pre><code>x = **y**</code></pre>')
  })

  it('escapes raw HTML written by the model', () => {
    expect(markdownToHtml('<script>alert(1)</script>')).not.toContain('<script')
  })

  it('renders formulas with the current KaTeX markup', () => {
    const html = markdownToHtml('行内 $a_r$ 与块级\n\n$$\\sum_{r \\in R} \\frac{1}{k + r}$$')

    expect(html).toContain('class="katex"')
    expect(html).toContain('class="katex-display"')
    expect(html).toContain('vlist')
  })

  it('renders \\[ \\] and \\( \\) formulas but leaves code untouched', () => {
    const html = markdownToHtml('块级：\\[ a^2 + b^2 = c^2 \\] 行内 \\(k_1\\)。\n\n`\\(not math\\)`')

    expect(html).toContain('class="katex-display"')
    expect(html).toContain('class="katex"')
    expect(html).toContain('<code>\\(not math\\)</code>')
    expect(normalizeMathDelimiters('```\n\\[x\\]\n```')).toBe('```\n\\[x\\]\n```')
  })
})
