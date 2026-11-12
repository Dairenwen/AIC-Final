import { createRouter, createWebHistory } from 'vue-router'

import CatalogView from '@/views/CatalogView.vue'
import ComplianceAgentView from '@/views/ComplianceAgentView.vue'
import AcademicTranslationAgentView from '@/views/AcademicTranslationAgentView.vue'
import AcademicFigureAgentView from '@/views/AcademicFigureAgentView.vue'
import AcademicDailyAgentView from '@/views/AcademicDailyAgentView.vue'
import AcademicSpaceView from '@/views/AcademicSpaceView.vue'
import ContributionRecommendationAgentView from '@/views/ContributionRecommendationAgentView.vue'
import HomeView from '@/views/HomeView.vue'
import InnovationAgentView from '@/views/InnovationAgentView.vue'
import SkillDetailView from '@/views/SkillDetailView.vue'
import KnowledgeBaseAdminView from '@/views/KnowledgeBaseAdminView.vue'
import LiteratureAgentView from '@/views/LiteratureAgentView.vue'
import LiteraturePptToolView from '@/views/LiteraturePptToolView.vue'
import FormulaImageToLatexView from '@/views/FormulaImageToLatexView.vue'
import ResearchToolView from '@/views/ResearchToolView.vue'
import ManuscriptAgentView from '@/views/ManuscriptAgentView.vue'
import LoginView from '@/views/LoginView.vue'
import PaperReadingAgentView from '@/views/PaperReadingAgentView.vue'
import PatentDraftingAgentView from '@/views/PatentDraftingAgentView.vue'
import ProfileView from '@/views/ProfileView.vue'
import ProjectsView from '@/views/ProjectsView.vue'
import ProjectWorkspaceView from '@/views/ProjectWorkspaceView.vue'
import ReviewerCommentsAgentView from '@/views/ReviewerCommentsAgentView.vue'
import SystemConfigView from '@/views/SystemConfigView.vue'
import AgentTeamsView from '@/views/AgentTeamsView.vue'
import { authState, restoreSession } from '@/auth/session'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { title: '登录', public: true } },
    { path: '/', name: 'home', component: HomeView, meta: { title: '首页' } },
    { path: '/projects', name: 'projects', component: ProjectsView, meta: { title: '科研项目' } },
    { path: '/projects/:id', name: 'project-workspace', component: ProjectWorkspaceView, meta: { title: '项目工作区' } },
    { path: '/academic-space', redirect: '/academic-space/knowledge' },
    { path: '/academic-space/knowledge', name: 'academic-space-knowledge', component: AcademicSpaceView, meta: { title: '我的知识库' } },
    {
      path: '/agents',
      name: 'agents',
      component: CatalogView,
      props: { kind: 'agents', title: '我的智能体', eyebrow: 'AI AGENTS' },
      meta: { title: '我的智能体' },
    },
    {
      path: '/agents/literature-search',
      name: 'literature-search',
      component: LiteratureAgentView,
      meta: { title: '文献检索' },
    },
    {
      path: '/agents/manuscript-assistance',
      name: 'manuscript-assistance',
      component: ManuscriptAgentView,
      meta: { title: '文稿辅助' },
    },
    {
      path: '/agents/innovation-point-generation',
      name: 'innovation-point-generation',
      component: InnovationAgentView,
      meta: { title: '创新点生成' },
    },
    {
      path: '/agents/paper-reading',
      name: 'paper-reading',
      component: PaperReadingAgentView,
      meta: { title: '论文精读' },
    },
    {
      path: '/agents/patent-drafting',
      name: 'patent-drafting',
      component: PatentDraftingAgentView,
      meta: { title: '专利撰写' },
    },
    {
      path: '/agents/academic-compliance',
      name: 'academic-compliance',
      component: ComplianceAgentView,
      meta: { title: '学术合规性检测' },
    },
    {
      path: '/agents/academic-daily',
      name: 'academic-daily',
      component: AcademicDailyAgentView,
      meta: { title: '学术速递' },
    },
    {
      path: '/agents/academic-figure',
      name: 'academic-figure',
      component: AcademicFigureAgentView,
      meta: { title: '绘图创作' },
    },
    {
      path: '/agents/academic-translation',
      name: 'academic-translation',
      component: AcademicTranslationAgentView,
      meta: { title: '学术翻译' },
    },
    {
      path: '/agents/reviewer-comments',
      name: 'reviewer-comments',
      component: ReviewerCommentsAgentView,
      meta: { title: '审稿意见解析与引导回复' },
    },
    {
      path: '/agents/contribution-recommendation',
      name: 'contribution-recommendation',
      component: ContributionRecommendationAgentView,
      meta: { title: '投稿推荐' },
    },
    {
      path: '/teams',
      name: 'teams',
      component: AgentTeamsView,
      meta: { title: '我的智囊团' },
    },
    {
      path: '/skills/:id',
      name: 'skill-detail',
      component: SkillDetailView,
      meta: { title: '科研技能详情' },
    },
    {
      path: '/tools',
      name: 'tools',
      component: CatalogView,
      props: { kind: 'tools', title: '科研工具集', eyebrow: 'RESEARCH TOOLS' },
      meta: { title: '科研工具集' },
    },
    {
      path: '/tools/formula-to-latex',
      name: 'formula-to-latex',
      component: FormulaImageToLatexView,
      meta: { title: '公式图片转 LaTeX' },
    },
    {
      path: '/tools/literature-ppt',
      name: 'literature-ppt',
      component: LiteraturePptToolView,
      meta: { title: '文献 PPT 绘制' },
    },
    { path: '/tools/citation-formatter', name: 'citation-formatter', component: ResearchToolView, meta: { title: '文献引用格式化' } },
    { path: '/tools/table-converter', name: 'table-converter', component: ResearchToolView, meta: { title: '科研表格转换' } },
    { path: '/tools/text-statistics', name: 'text-statistics', component: ResearchToolView, meta: { title: '学术文本统计' } },
    { path: '/tools/markdown-to-docx', name: 'markdown-to-docx', component: ResearchToolView, meta: { title: 'Markdown 转 Word' } },
    {
      path: '/skills',
      name: 'skills',
      component: CatalogView,
      props: { kind: 'skills', title: '科研技能库', eyebrow: 'RESEARCH SKILLS' },
      meta: { title: '科研技能库' },
    },
    { path: '/profile', name: 'profile', component: ProfileView, meta: { title: '个人中心' } },
    { path: '/models', redirect: { path: '/profile', query: { tab: 'model' } } },
    {
      path: '/admin',
      redirect: '/admin/system/dashboard',
    },
    {
      path: '/admin/system/:section(dashboard|knowledge-dashboard|exceptions|audit|permissions)',
      name: 'system-config',
      component: SystemConfigView,
      meta: { title: '系统配置', roles: ['system_admin'] },
    },
    {
      path: '/admin/knowledge-base/dashboard',
      redirect: '/admin/system/knowledge-dashboard',
    },
    {
      path: '/admin/knowledge-base/qa-generate',
      redirect: '/admin/knowledge-base/training-set',
    },
    {
      path: '/admin/knowledge-base/qa-review',
      redirect: '/admin/knowledge-base/training-set',
    },
    {
      path: '/admin/knowledge-base/exceptions',
      redirect: '/admin/system/exceptions',
    },
    {
      path: '/admin/knowledge-base/audit',
      redirect: '/admin/system/audit',
    },
    {
      path: '/admin/knowledge-base/permissions',
      redirect: '/admin/system/permissions',
    },
    {
      path: '/admin/knowledge-base/:section(knowledge|training-set)',
      name: 'knowledge-base-admin',
      component: KnowledgeBaseAdminView,
      meta: { title: '知识库管理平台', roles: ['system_admin'] },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const authenticated = await restoreSession()
  if (to.meta.public) {
    if (!authenticated) return true
    return safeRedirect(to.query.redirect)
  }
  if (!authenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  const roles = to.meta.roles as string[] | undefined
  if (roles && (!authState.user || !roles.includes(authState.user.role))) return { name: 'home' }
  return true
})

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? '工作台')} - 智研`
})

export default router

function safeRedirect(value: unknown) {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
}
