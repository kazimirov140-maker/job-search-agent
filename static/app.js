document.addEventListener('DOMContentLoaded', () => {
    const btnRun = document.getElementById('btn-run');
    const statusIndicator = document.getElementById('status-indicator');
    const statusText = statusIndicator.querySelector('.status-text');
    const terminalWindow = document.getElementById('terminal-window');
    const activeTask = document.getElementById('active-task');
    const jobsList = document.getElementById('jobs-list');
    const btnRefresh = document.getElementById('btn-refresh');
    const btnStop = document.getElementById('btn-stop');
    const funnelList = document.getElementById('funnel-list');

    const valSeen = document.getElementById('val-seen');
    const valHigh = document.getElementById('val-high');
    const valRejected = document.getElementById('val-rejected');

    let eventSource = null;

    // Check initial status
    fetchStatus();
    fetchStats();
    fetchJobs();
    fetchFunnel();

    // Event Listeners
    btnRun.addEventListener('click', () => runAgent('standard'));

    const btnRunEnAsync = document.getElementById('btn-run-en-async');
    if (btnRunEnAsync) {
        btnRunEnAsync.addEventListener('click', () => runAgent('en_async'));
    }

    if (btnStop) {
        btnStop.addEventListener('click', stopAgent);
    }

    btnRefresh.addEventListener('click', () => {
        fetchJobs();
        fetchStats();
        fetchFunnel();
    });

    function setRunningState(isRunning, mode) {
        if (isRunning) {
            statusIndicator.classList.add('running');
            statusText.textContent = mode ? 'Running · ' + mode : 'Running';
            btnRun.disabled = true;
            btnRun.textContent = 'Agent is Active';
            if (btnStop) { btnStop.style.display = ''; btnStop.disabled = false; }
            startLogStream();
        } else {
            statusIndicator.classList.remove('running');
            statusText.textContent = 'Idle';
            btnRun.disabled = false;
            btnRun.textContent = 'Launch Agent';
            if (btnStop) { btnStop.style.display = 'none'; }
            activeTask.textContent = 'Waiting...';
            if (eventSource) {
                eventSource.close();
                eventSource = null;
            }
            fetchStats();
            fetchJobs();
            fetchFunnel();
        }
    }

    async function stopAgent() {
        if (btnStop) { btnStop.disabled = true; }
        try {
            await fetch('/api/stop', { method: 'POST' });
        } catch (e) {
            console.error("Failed to stop agent:", e);
        }
        setTimeout(fetchStatus, 1500);
    }

    async function fetchStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            setRunningState(data.running, data.mode);
        } catch (e) {
            console.error("Failed to fetch status:", e);
        }
    }

    async function runAgent(mode = 'standard') {
        try {
            terminalWindow.innerHTML = ''; // Clear terminal on new run
            const res = await fetch('/api/run', { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: mode })
            });
            if (res.ok) {
                setRunningState(true, mode);
            }
        } catch (e) {
            console.error("Failed to run agent:", e);
        }
    }

    function startLogStream() {
        if (eventSource) return;
        eventSource = new EventSource('/api/stream-logs');

        eventSource.onmessage = (event) => {
            const line = event.data;
            appendLog(line);
            parseLogForActiveTask(line);

            // Auto-detect finish
            if (line.includes('--- Bot Finished')) {
                setTimeout(fetchStatus, 1000); // Check status again to reset UI
            }
        };

        eventSource.onerror = (e) => {
            console.error("SSE Error:", e);
        };
    }

    function appendLog(text) {
        const div = document.createElement('div');
        div.className = 'log-line';
        
        let formatted = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        
        // Simple colorization
        if (formatted.includes('[INFO]')) {
            div.classList.add('log-info');
        } else if (formatted.includes('[WARNING]')) {
            div.classList.add('log-warning');
        } else if (formatted.includes('[ERROR]')) {
            div.classList.add('log-error');
        }

        if (formatted.includes('── Сбор:') || formatted.includes('Оценка:')) {
            div.classList.add('log-highlight');
        }

        div.innerHTML = formatted;
        terminalWindow.appendChild(div);
        terminalWindow.scrollTop = terminalWindow.scrollHeight;
    }

    function parseLogForActiveTask(line) {
        // Extract scraping source
        const scrapeMatch = line.match(/── Сбор:\s*(.+)\s*──/);
        if (scrapeMatch) {
            activeTask.innerHTML = `Scraping: <strong>${scrapeMatch[1]}</strong>`;
            return;
        }

        // Extract LLM eval progress
        const evalMatch = line.match(/\[(\d+\/\d+)\] Оценка:\s*(.*)/);
        if (evalMatch) {
            activeTask.innerHTML = `Evaluating [${evalMatch[1]}]: <strong>${evalMatch[2].substring(0, 40)}...</strong>`;
            return;
        }
    }

    async function fetchStats() {
        try {
            const res = await fetch('/api/stats');
            const data = await res.json();
            valSeen.textContent = data.total_seen;
            valHigh.textContent = data.total_high_score;
            valRejected.textContent = data.total_rejected;
        } catch (e) {
            console.error("Failed to fetch stats:", e);
        }
    }

    async function fetchJobs() {
        try {
            const res = await fetch('/api/jobs');
            const jobs = await res.json();
            
            jobsList.innerHTML = '';
            
            if (jobs.length === 0) {
                jobsList.innerHTML = '<div class="loading-state">No jobs found yet.</div>';
                return;
            }

            jobs.forEach(job => {
                const card = document.createElement(job.url ? 'a' : 'div');
                card.className = 'job-card';
                if (job.url) {
                    card.href = job.url;
                    card.target = '_blank';
                    card.rel = 'noopener noreferrer';
                }
                const date = (job.date_added || '').split(' ')[0];
                card.innerHTML = `
                    <div class="job-header">
                        <div class="job-title">${escapeHtml(job.title)}</div>
                        <div class="job-score">${job.score}</div>
                    </div>
                    <div class="job-company">${escapeHtml(job.company)}</div>
                    <div class="job-meta">
                        <span>${escapeHtml(job.source)}</span>
                        <span>${escapeHtml(job.status || '')}</span>
                        <span>${escapeHtml(date)}</span>
                    </div>
                `;
                jobsList.appendChild(card);
            });
        } catch (e) {
            console.error("Failed to fetch jobs:", e);
            jobsList.innerHTML = '<div class="loading-state">Error loading jobs.</div>';
        }
    }

    // Воронка отказов: отвечает на вопрос «почему из 1700 вакансий
    // осталось три» — без неё непонятно, фильтр слишком строгий или
    // источники не отдали ничего подходящего.
    async function fetchFunnel() {
        if (!funnelList) return;
        try {
            const res = await fetch('/api/funnel');
            const data = await res.json();
            const reasons = data.reject_reasons || [];
            const total = reasons.reduce((sum, r) => sum + r.count, 0);

            if (!reasons.length) {
                funnelList.innerHTML = '<div class="loading-state">No rejections recorded yet.</div>';
                return;
            }

            funnelList.innerHTML = reasons.map(r => {
                const pct = total ? Math.round((r.count / total) * 100) : 0;
                return `
                    <div class="funnel-row">
                        <div class="funnel-label">${escapeHtml(r.reason)}</div>
                        <div class="funnel-bar"><div class="funnel-fill" style="width:${pct}%"></div></div>
                        <div class="funnel-count">${r.count}</div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            console.error("Failed to fetch funnel:", e);
            funnelList.innerHTML = '<div class="loading-state">Error loading funnel.</div>';
        }
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
});
