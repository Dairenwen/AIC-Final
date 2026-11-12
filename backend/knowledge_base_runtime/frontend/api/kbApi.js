const api = {
    // 后端地址（部署时修改）
    baseURL: '/api/v1',

    async request(path, options = {}) {
        const res = await fetch(`${this.baseURL}${path}`, options);
        if (!res.ok) {
            let message = `request failed (${res.status})`;
            try {
                const payload = await res.json();
                message = payload.error || payload.message || message;
            } catch (_) {}
            throw new Error(message);
        }
        return res.status === 204 ? null : res.json();
    },

    // ---- 内存模拟数据（后端未就绪时使用） ----
    _mockStore: {
        users: [],
        _userIdSeq: 1,
    },

    /*
    ===== 登录 API（暂未启用）=====
    恢复登录时取消注释此段即可

    tokenKey: 'zhiyan_auth_token',
    userKey: 'zhiyan_auth_user',

    getToken() {
        return localStorage.getItem(this.tokenKey);
    },

    async login(username, password) {
        console.log('[API] POST /api/v1/auth/login', { username });
        // TODO: 后端就绪后替换为真实请求
        // const res = await fetch(`${this.baseURL}/auth/login`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ username, password }) });
        // return res.json();

        // 临时模拟登录（密码任意非空即可）
        const mockUsers = {
            'admin':     { username: 'admin',     role: '系统管理员', api_key: 'sk-admin-'  + Date.now().toString(36) },
            'dataadmin': { username: 'dataadmin', role: '数据管理员', api_key: 'sk-data-'  + Date.now().toString(36) },
            'user':      { username: 'user',      role: '普通用户',   api_key: 'sk-user-'  + Date.now().toString(36) },
        };
        const user = mockUsers[username];
        if (!user || !password) {
            throw new Error('用户名或密码错误');
        }
        return { access_token: 'mock-jwt-' + username + '-' + Date.now(), user: { ...user } };
    },

    async getMe() {
        console.log('[API] GET /api/v1/auth/me');
        // TODO: 后端就绪后替换为真实请求
        // const token = this.getToken();
        // const res = await fetch(`${this.baseURL}/auth/me`, { headers: { 'Authorization': 'Bearer ' + token } });
        // return res.json();

        const stored = localStorage.getItem(this.userKey);
        if (stored) {
            try { return JSON.parse(stored); } catch (_) {}
        }
        throw new Error('未登录或会话已过期');
    },
    ===== 登录 API 结束 =====
    */

    /* ---- 搜索 ---- */
    /*
     * POST /api/v1/search
     * Request:  { query, mode, filters: { year_start, year_end, ccf_level, research_area, subfield }, page, size }
     * Response: { total, list: [{ id, title, author/authors, publish_venue/venue, publish_year/year,
     *                              abstract_preview, ccf_level, citation_count, pdf_url, chunks }] }
     */
    async search(params) {
        console.log('[API] POST /api/v1/search', params);
        const res = await fetch(`${this.baseURL}/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        });
        if (!res.ok) {
            let message = 'search request failed';
            try {
                const error = await res.json();
                message = error.message || error.error || message;
            } catch (_) {}
            throw new Error(message);
        }
        return res.json();
    },

    /* ---- 文献库 ---- */
    /*
     * GET /api/v1/collections
     * Response: [{ id, user_id, collection_name, paper_count, created_at }]
     */
    async getCollections() {
        console.log('[API] GET /api/v1/collections');
        return this.request('/collections');
    },

    /*
     * POST /api/v1/collections
     * Request:  { collection_name }
     * Response: { id, collection_name, created_at }
     */
    async createCollection(data) {
        console.log('[API] POST /api/v1/collections', data);
        return this.request('/collections', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
    },

    /*
     * GET /api/v1/collections/{id}/papers
     * Response: [{ paper_id, title, author, year, note }]
     */
    async getCollectionPapers(collectionId) {
        console.log('[API] GET /api/v1/collections/' + collectionId + '/papers');
        return this.request(`/collections/${encodeURIComponent(collectionId)}/papers`);
    },

    /*
     * POST /api/v1/collections/{id}/papers
     * Request:  { paper_id, note }
     */
    async addPaperToCollection(collectionId, data) {
        console.log('[API] POST /api/v1/collections/' + collectionId + '/papers', data);
        return this.request(`/collections/${encodeURIComponent(collectionId)}/papers`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
    },

    /*
     * DELETE /api/v1/collections/{id}/papers/{paper_id}
     */
    async removePaperFromCollection(collectionId, paperId) {
        console.log('[API] DELETE /api/v1/collections/' + collectionId + '/papers/' + paperId);
        return this.request(`/collections/${encodeURIComponent(collectionId)}/papers/${encodeURIComponent(paperId)}`, {
            method: 'DELETE',
        });
    },

    /* ---- 上传 ---- */
    /*
     * POST /api/v1/upload/pdf
     * Request:  multipart/form-data (file)
     * Response: { task_id, status: "parsing" }
     */
    async uploadPdf(formData) {
        console.log('[API] POST /api/v1/upload/pdf');
        return this.request('/upload/pdf', { method: 'POST', body: formData });
    },

    /*
     * POST /api/v1/upload/confirm
     * Request:  { task_id, title, authors: [], confirm: true }
     * Response: { status: "confirmed", paper_id }
     */
    async confirmUpload(data) {
        console.log('[API] POST /api/v1/upload/confirm', data);
        return this.request('/upload/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
    },

    /*
     * GET /api/v1/upload/status/{task_id}
     * Response: { task_id, status, meta: { title, authors } }
     */
    async getUploadStatus(taskId) {
        console.log('[API] GET /api/v1/upload/status/' + taskId);
        return this.request(`/upload/status/${encodeURIComponent(taskId)}`);
    },

    /* ---- Agent ---- */
    /*
     * POST /api/v1/agent/invoke
     * Request:  { agent_type, paper_ids: [], extra_params: { focus, prompt } }
     * Response: { job_id }
     */
    async invokeAgent(data) {
        console.log('[API] POST /api/v1/agent/invoke', data);
        const res = await fetch(`${this.baseURL}/agent/invoke`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('agent invoke request failed');
        return res.json();
    },

    /*
     * GET /api/v1/agent/status/{job_id}
     * Response: { job_id, status: "RUNNING"|"SUCCESS"|"FAILED", result, error }
     */
    async getAgentStatus(jobId) {
        console.log('[API] GET /api/v1/agent/status/' + jobId);
        const res = await fetch(`${this.baseURL}/agent/status/${encodeURIComponent(jobId)}`);
        if (!res.ok) throw new Error('agent status request failed');
        return res.json();
    },

    /* ---- Dashboard (管理端) ---- */
    /*
     * GET /api/v1/admin/dashboard
     * Response: { total_papers, total_venues, parsed_papers, vector_chunks, exception_papers, active_users }
     */
    async getDashboard() {
        console.log('[API] GET /api/v1/admin/dashboard');
        const res = await fetch(`${this.baseURL}/admin/dashboard`);
        if (!res.ok) throw new Error('dashboard request failed');
        return res.json();
    },

    /*
     * GET /api/v1/admin/service-health
     * Response: [{ name, healthy, latency, uptime }]
     */
    async getServiceHealth() {
        console.log('[API] GET /api/v1/admin/service-health');
        const res = await fetch(`${this.baseURL}/admin/service-health`);
        if (!res.ok) throw new Error('service health request failed');
        return res.json();
    },

    /* ---- 会议/期刊管理 ---- */
    /*
     * GET /api/v1/admin/venues?search=&ccf_level=&page=&size=
     * Response: { total, list: [{ id, short_name, full_name, ccf_level, type, website, paper_count }] }
     */
    async getVenues(params = {}) {
        console.log('[API] GET /api/v1/admin/venues', params);
        // TODO
        return { total: 0, list: [] };
    },

    /*
     * POST /api/v1/admin/venues
     * Request:  { short_name, full_name, ccf_level, type, website }
     */
    async createVenue(data) {
        console.log('[API] POST /api/v1/admin/venues', data);
        // TODO
    },

    /*
     * PUT /api/v1/admin/venues/{id}
     */
    async updateVenue(id, data) {
        console.log('[API] PUT /api/v1/admin/venues/' + id, data);
        // TODO
    },

    /*
     * DELETE /api/v1/admin/venues/{id}
     */
    async deleteVenue(id) {
        console.log('[API] DELETE /api/v1/admin/venues/' + id);
        // TODO
    },

    /* ---- 论文管理 ---- */
    /*
     * GET /api/v1/admin/papers?search=&parse_status=&page=&size=
     * Response: { total, list: [{ id, title, author, publish_year, publish_venue, parse_status, ... }] }
     */
    async getPapers(params = {}) {
        console.log('[API] GET /api/v1/admin/papers', params);
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const res = await fetch(`${this.baseURL}/admin/papers?${query.toString()}`);
        if (!res.ok) throw new Error('papers request failed');
        return res.json();
    },

    /*
     * POST /api/v1/admin/papers
     */
    async createPaper(data) {
        console.log('[API] POST /api/v1/admin/papers', data);
        const res = await fetch(`${this.baseURL}/admin/papers`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('create paper request failed');
        return res.json();
    },

    /*
     * PUT /api/v1/admin/papers/{id}
     */
    async updatePaper(id, data) {
        console.log('[API] PUT /api/v1/admin/papers/' + id, data);
        const res = await fetch(`${this.baseURL}/admin/papers/${encodeURIComponent(id)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('update paper request failed');
        return res.json();
    },

    /*
     * DELETE /api/v1/admin/papers/{id}
     */
    async deletePaper(id) {
        console.log('[API] DELETE /api/v1/admin/papers/' + id);
        const res = await fetch(`${this.baseURL}/admin/papers/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('delete paper request failed');
        return res.json();
    },

    /* ---- 知识库管理 ---- */
    /*
     * GET /api/v1/admin/knowledge?domain=&search=&ccf_level=&sliced=&page=&size=
     * Response: { total, list: [{ id, short_name, full_name, ccf_level, type, website, is_sliced, ... }] }
     */
    async getKnowledgePapers(params = {}) {
        console.log('[API] GET /api/v1/admin/knowledge', params);
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const res = await fetch(`${this.baseURL}/admin/knowledge?${query.toString()}`);
        if (!res.ok) throw new Error('knowledge request failed');
        return res.json();
    },

    async getPaperChunks(paperId, params = {}) {
        console.log('[API] GET /api/v1/admin/knowledge/' + paperId + '/chunks', params);
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const res = await fetch(`${this.baseURL}/admin/knowledge/${encodeURIComponent(paperId)}/chunks?${query.toString()}`);
        if (!res.ok) throw new Error('paper chunks request failed');
        return res.json();
    },

    /*
     * POST /api/v1/admin/knowledge/slice
     * Request:  { paper_ids: [], method: 'fixed_boundary_v1', strategy: 'fixed_boundary_v1' }
     * Response: { task_id, status }
     */
    async slicePapers(data) {
        console.log('[API] POST /api/v1/admin/knowledge/slice', data);
        const res = await fetch(`${this.baseURL}/admin/knowledge/slice`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('slice papers request failed');
        return res.json();
    },

    /* ---- 异常处理 ---- */
    /*
     * GET /api/v1/admin/exceptions?page=&size=
     * Response: { total, list: [{ id, paper_id, title, error_type, error_time, retry_count }] }
     */
    async getExceptions(params = {}) {
        console.log('[API] GET /api/v1/admin/exceptions', params);
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const res = await fetch(`${this.baseURL}/admin/exceptions?${query.toString()}`);
        if (!res.ok) throw new Error('exceptions request failed');
        return res.json();
    },

    /*
     * POST /api/v1/admin/exceptions/{id}/retry
     */
    async retryParse(id) {
        console.log('[API] POST /api/v1/admin/exceptions/' + id + '/retry');
        const res = await fetch(`${this.baseURL}/admin/exceptions/${encodeURIComponent(id)}/retry`, { method: 'POST' });
        if (!res.ok) throw new Error('retry parse request failed');
        return res.json();
    },

    /* ---- 审计日志 ---- */
    /*
     * GET /api/v1/admin/audit-logs?action=&page=&size=
     * Response: { total, list: [{ id, timestamp, user_ip, action, resource, detail }] }
     */
    async getAuditLogs(params = {}) {
        console.log('[API] GET /api/v1/admin/audit-logs', params);
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const res = await fetch(`${this.baseURL}/admin/audit-logs?${query.toString()}`);
        if (!res.ok) throw new Error('audit logs request failed');
        return res.json();
    },

    /*
     * GET /api/v1/admin/audit-stats
     * Response: { total, active_users, actions, resources, recent }
     */
    async getAuditStats() {
        console.log('[API] GET /api/v1/admin/audit-stats');
        const res = await fetch(`${this.baseURL}/admin/audit-stats`);
        if (!res.ok) throw new Error('audit stats request failed');
        return res.json();
    },

    /* ---- 权限管理 ---- */
    /*
     * GET /api/v1/admin/users?page=&size=
     * Response: { total, list: [{ id, username, role, api_key, call_count, active }] }
     */
    async getUsers(params = {}) {
        console.log('[API] GET /api/v1/admin/users', params);
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const res = await fetch(`${this.baseURL}/admin/users?${query.toString()}`);
        if (!res.ok) throw new Error('users request failed');
        return res.json();
    },

    /*
     * PUT /api/v1/admin/users/{id}/role
     */
    async updateUserRole(id, role) {
        console.log('[API] PUT /api/v1/admin/users/' + id + '/role', { role });
        const res = await fetch(`${this.baseURL}/admin/users/${id}/role`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role }),
        });
        if (!res.ok) throw new Error('update role request failed');
        return res.json();
    },

    /*
     * PUT /api/v1/admin/users/{id}/status
     */
    async toggleUserStatus(id, active) {
        console.log('[API] PUT /api/v1/admin/users/' + id + '/status', { active });
        const res = await fetch(`${this.baseURL}/admin/users/${id}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active }),
        });
        if (!res.ok) throw new Error('update status request failed');
        return res.json();
    },

    /*
     * POST /api/v1/admin/users/{id}/reset-api-key
     */
    async resetApiKey(id) {
        console.log('[API] POST /api/v1/admin/users/' + id + '/reset-api-key');
        const res = await fetch(`${this.baseURL}/admin/users/${id}/reset-api-key`, { method: 'POST' });
        if (!res.ok) throw new Error('reset api key request failed');
        return res.json();
    },

    /*
     * POST /api/v1/admin/users
     */
    async createUser(data) {
        console.log('[API] POST /api/v1/admin/users', data);
        const res = await fetch(`${this.baseURL}/admin/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('create user request failed');
        return res.json();
    },

    /*
     * DELETE /api/v1/admin/users/{id}
     */
    async deleteUser(id) {
        console.log('[API] DELETE /api/v1/admin/users/' + id);
        const res = await fetch(`${this.baseURL}/admin/users/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('delete user request failed');
        return res.json();
    },

    async getQaChunks(params = {}) {
        console.log('[API] GET /api/v1/admin/qa/chunks', params);
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const res = await fetch(`${this.baseURL}/admin/qa/chunks?${query.toString()}`);
        if (!res.ok) throw new Error('qa chunks request failed');
        return res.json();
    },

    async getQaGenerationConfig() {
        const res = await fetch(`${this.baseURL}/admin/qa/config`);
        if (!res.ok) throw new Error('qa generation config request failed');
        return res.json();
    },

    async generateQA(data) {
        console.log('[API] POST /api/v1/admin/qa/generate', data);
        const res = await fetch(`${this.baseURL}/admin/qa/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('qa generate request failed');
        return res.json();
    },

    async generateDPO(data) {
        console.log('[API] POST /api/v1/admin/qa/generate-dpo', data);
        const res = await fetch(`${this.baseURL}/admin/qa/generate-dpo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('dpo generate request failed');
        return res.json();
    },

    async downloadDPO(runId) {
        const query = new URLSearchParams({ run_id: runId });
        const res = await fetch(`${this.baseURL}/admin/qa/dpo/export?${query.toString()}`);
        if (!res.ok) throw new Error('dpo export request failed');
        return res.blob();
    },

    async submitQaToReview(data) {
        console.log('[API] POST /api/v1/admin/qa/submit-review', data);
        const res = await fetch(`${this.baseURL}/admin/qa/submit-review`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('qa submit review request failed');
        return res.json();
    },

    async manualReview(data) {
        console.log('[API] POST /api/v1/admin/qa/manual-review', data);
        const res = await fetch(`${this.baseURL}/admin/qa/manual-review`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error('qa manual review request failed');
        return res.json();
    },
};

const qualityApi = {
    storageKey: 'zhiyan_quality_center_base_url',
    defaultBaseURL: 'http://127.0.0.1:8000',

    getBaseURL() {
        return (localStorage.getItem(this.storageKey) || this.defaultBaseURL).replace(/\/+$/, '');
    },
    setBaseURL(url) {
        const normalized = (url || this.defaultBaseURL).trim().replace(/\/+$/, '');
        localStorage.setItem(this.storageKey, normalized);
        return normalized;
    },
    url(path, params = {}) {
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') query.set(key, value);
        });
        const suffix = query.toString() ? `?${query.toString()}` : '';
        return `${this.getBaseURL()}${path}${suffix}`;
    },
    async request(path, options = {}) {
        const res = await fetch(this.url(path, options.params || {}), options.fetchOptions || {});
        const text = await res.text();
        let payload = null;
        if (text) {
            try { payload = JSON.parse(text); }
            catch (_) { payload = { message: text }; }
        }
        if (!res.ok) {
            const code = payload?.code || payload?.error_code || '';
            const message = payload?.message || payload?.error || `${res.status} ${res.statusText}`;
            const err = new Error(code ? `${code}: ${message}` : message);
            err.status = res.status;
            err.payload = payload;
            throw err;
        }
        return payload || {};
    },
    getGenerationConfig() {
        return this.request('/api/operations/generation-config');
    },
    importSnapshot(file) {
        const form = new FormData();
        form.append('snapshot_zip', file);
        return this.request('/api/operations/snapshot-imports', { fetchOptions: { method: 'POST', body: form } });
    },
    getSnapshotImport(id) {
        return this.request(`/api/operations/snapshot-imports/${encodeURIComponent(id)}`);
    },
    getSnapshotChunks(id, params = {}) {
        return this.request(`/api/operations/snapshot-imports/${encodeURIComponent(id)}/chunks`, { params });
    },
    getSnapshotChunk(id, chunkId) {
        return this.request(`/api/operations/snapshot-imports/${encodeURIComponent(id)}/chunks/${encodeURIComponent(chunkId)}`);
    },
    createGenerationRun(data) {
        return this.request('/api/operations/generation-runs', {
            fetchOptions: { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) },
        });
    },
    getGenerationRun(runId) {
        return this.request(`/api/operations/generation-runs/${encodeURIComponent(runId)}`);
    },
    getRunOutcomes(runId, params = {}) {
        return this.request(`/api/operations/generation-runs/${encodeURIComponent(runId)}/outcomes`, { params });
    },
    getRunCandidates(runId, params = {}) {
        return this.request(`/api/operations/generation-runs/${encodeURIComponent(runId)}/candidates`, { params });
    },
    getRunQualitySummary(runId) {
        return this.request(`/api/operations/generation-runs/${encodeURIComponent(runId)}/quality-summary`);
    },
    async downloadRunCandidates(runId) {
        const res = await fetch(this.url(`/api/operations/generation-runs/${encodeURIComponent(runId)}/candidates/export`));
        if (!res.ok) throw new Error(`candidate export failed: ${res.status}`);
        return { blob: await res.blob(), sha256: res.headers.get('X-Content-SHA256') };
    },
    createReviewSession(data) {
        return this.request('/api/operations/review-sessions', {
            fetchOptions: { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) },
        });
    },
    listReviewSessions(params = {}) {
        return this.request('/api/operations/review-sessions', { params });
    },
    listReviewItems(sessionId, params = {}) {
        return this.request(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/items`, { params });
    },
    getReviewItem(sessionId, itemId) {
        return this.request(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(itemId)}`);
    },
    getReasonCodes() {
        return this.request('/api/operations/review-reason-codes');
    },
    submitReviewDecision(sessionId, itemId, data) {
        return this.request(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(itemId)}/decisions`, {
            fetchOptions: { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) },
        });
    },
    submitBatchDecisions(sessionId, data) {
        return this.request(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/batch-decisions`, {
            fetchOptions: { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) },
        });
    },
    getReviewSummary(sessionId) {
        return this.request(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/summary`);
    },
    getReviewValidation(sessionId) {
        return this.request(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/validation`);
    },
    closeReviewSession(sessionId) {
        return this.request(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/close`, { fetchOptions: { method: 'POST' } });
    },
    async downloadReviewExport(sessionId) {
        const res = await fetch(this.url(`/api/operations/review-sessions/${encodeURIComponent(sessionId)}/export`));
        if (!res.ok) throw new Error(`review export failed: ${res.status}`);
        return res.blob();
    },
};

/* ========================================================================
 *  Vue 应用
 * ======================================================================== */

window.api = api;
window.qualityApi = qualityApi;
