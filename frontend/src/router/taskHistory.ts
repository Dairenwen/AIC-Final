import type { RouteLocationRaw } from 'vue-router'

import type { HistoryItem } from '@/types'

const agentRouteNames: Record<string, string> = {
  literature_search: 'literature-search',
  manuscript_assistance: 'manuscript-assistance',
  innovation_point_generation: 'innovation-point-generation',
  paper_reading: 'paper-reading',
  academic_compliance: 'academic-compliance',
  academic_translation: 'academic-translation',
  patent_drafting: 'patent-drafting',
  academic_figure: 'academic-figure',
  arxiv_daily: 'academic-daily',
}

export function taskHistoryLocation(item: Pick<HistoryItem, 'id' | 'agentCode'>): RouteLocationRaw {
  const name = agentRouteNames[item.agentCode]
  if (!name) return { name: 'agents' }

  return {
    name,
    query: { task: item.id },
  }
}
