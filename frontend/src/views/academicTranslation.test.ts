import { describe, expect, it } from 'vitest'

import type { ResearchTask, TranslationFile, TranslationTaskEvent } from '@/types'

import {
  buildTranslationFormState,
  collectTranslationQualityIssues,
  collectTranslationWarnings,
  deriveTranslationStatusSummary,
  parseTranslationGlossary,
  selectTranslationPreviewState,
  sortTranslationFiles,
  validateTranslationSubmission,
} from './academicTranslation'

function createTask(overrides: Partial<ResearchTask> = {}): ResearchTask {
  return {
    id: 'task-1',
    title: 'Academic Translation',
    prompt: 'translate this paper',
    status: 'SUCCEEDED',
    progress: 100,
    current_step: '学术翻译完成',
    output: {},
    ...overrides,
  }
}

describe('academicTranslation helpers', () => {
  it('parses glossary JSON into trimmed key-value pairs', () => {
    expect(parseTranslationGlossary('{ " foundation model ": " 基础模型 " }')).toEqual({
      'foundation model': '基础模型',
    })
  })

  it('returns actionable validation messages before submission', () => {
    const result = validateTranslationSubmission({
      file: { name: 'paper.docx' },
      sourceLang: 'en',
      targetLang: 'en',
      preserveLayout: true,
      glossaryText: 'broken-json',
      parallel: 8,
    })

    expect(result.glossary).toBeNull()
    expect(result.messages).toEqual([
      '请将“源语言”和“目标语言”设置为不同语言，例如 English -> 简体中文。',
      '如需保留 PDF 原版式，请上传 PDF 文件，或关闭“保留 PDF 原版式”。',
      '并行数需在 1 到 5 之间，请重新选择。',
      '术语表需要填写为合法 JSON 对象，例如 {"foundation model":"基础模型"}。',
    ])
  })

  it('restores the full translation form from saved task configuration', () => {
    const task = createTask({
      prompt: '请保持公式不变',
      output: {
        translation_request: {
          file_name: 'paper.pdf',
          file_type: 'pdf',
          source_lang: 'de',
          target_lang: 'zh',
          precision: 'submission',
          glossary: { diffusion: '扩散', transformer: 'Transformer' },
          parallel: 4,
          preserve_pdf_layout: true,
          bilingual: true,
          translate_figures: true,
        },
      },
    })

    expect(buildTranslationFormState(task)).toEqual({
      query: '请保持公式不变',
      sourceLang: 'de',
      targetLang: 'zh',
      precision: 'submission',
      glossaryText: '{\n  "diffusion": "扩散",\n  "transformer": "Transformer"\n}',
      bilingual: true,
      preserveLayout: true,
      translateFigures: true,
      parallel: 4,
    })
  })

  it('keeps success state but marks warnings separately after completion', () => {
    const task = createTask({
      output: {
        translation_quality: {
          total_segments: 12,
          translated_segments: 12,
          terminology_violations: ['term-3'],
          warnings: ['图表标签已回退为英文'],
        },
        translation_warnings: ['PDF 目录页未生成预览'],
      },
      started_at: '2026-08-05T10:00:00Z',
      finished_at: '2026-08-05T10:01:15Z',
    })

    expect(collectTranslationQualityIssues(task.output.translation_quality)).toEqual(['term-3'])
    expect(collectTranslationWarnings(task.output.translation_quality, task.output.translation_warnings)).toEqual([
      'PDF 目录页未生成预览',
      '图表标签已回退为英文',
    ])

    expect(deriveTranslationStatusSummary(task, [])).toEqual({
      tone: 'warning',
      label: '已完成，存在警告',
      detail: '任务已完成，但仍有1 个待复核项、2 条运行提示。请先查看“质量检查”，确认是否需要重新翻译或人工复核。',
      stage: '学术翻译完成',
      elapsedLabel: '1分15秒',
    })
  })

  it('classifies failed runs with actionable guidance', () => {
    const task = createTask({
      status: 'FAILED',
      progress: 52,
      current_step: '正在执行学术语境翻译与术语一致性约束',
      error: '学术翻译核心工作流执行失败：运行超时',
      started_at: '2026-08-05T10:00:00Z',
    })
    const events: TranslationTaskEvent[] = [
      {
        sequence: 4,
        type: 'translation.heartbeat',
        progress: 66,
        message: '学术翻译仍在执行，已用时 180 秒',
        elapsed_seconds: 180,
      },
      {
        sequence: 5,
        type: 'task.failed',
        progress: 66,
        message: '学术翻译核心工作流执行失败：运行超时',
      },
    ]

    expect(deriveTranslationStatusSummary(task, events)).toEqual({
      tone: 'failed',
      label: '任务失败',
      detail: '学术翻译核心工作流执行失败：运行超时 请缩小文档规模、关闭“保留 PDF 原版式”，或稍后重试。',
      stage: '正在执行学术语境翻译与术语一致性约束',
      elapsedLabel: '3分',
    })
  })

  it('prefers PDF preview and keeps the file list aligned with the preview target', () => {
    const files: TranslationFile[] = [
      { kind: 'translation_report', label: '翻译质量报告', file_name: 'report.md', size: 12 },
      { kind: 'pdf_bilingual', label: '双语对照 PDF', file_name: 'paper-bilingual.pdf', size: 128 },
      { kind: 'monolingual_markdown', label: '单语译文 Markdown', file_name: 'paper.md', size: 64 },
    ]
    const preview = selectTranslationPreviewState(files, [], {
      file_name: 'paper.pdf',
      file_type: 'pdf',
      source_lang: 'en',
      target_lang: 'zh',
      precision: 'reading',
      bilingual: true,
      preserve_pdf_layout: true,
      translate_figures: false,
    })

    expect(preview).toEqual({
      mode: 'pdf',
      file: files[1],
    })
    expect(sortTranslationFiles(files, preview.file).map((item) => item.kind)).toEqual([
      'pdf_bilingual',
      'monolingual_markdown',
      'translation_report',
    ])
  })

  it('falls back to segment preview when no PDF artifact exists', () => {
    expect(
      selectTranslationPreviewState(
        [{ kind: 'monolingual_markdown', label: '单语译文 Markdown', file_name: 'paper.md', size: 64 }],
        [{ source_text: 'A', translated_text: '甲' }],
      ),
    ).toEqual({
      mode: 'segments',
      file: null,
    })
  })
})
