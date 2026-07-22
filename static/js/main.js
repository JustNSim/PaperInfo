/**
 * PaperInfo 前端交互脚本
 */

$(document).ready(function() {
    // 初始化工具提示
    $('[data-bs-toggle="tooltip"]').tooltip();

    // 确认对话框
    $('.confirm-action').on('click', function(e) {
        if (!confirm($(this).data('confirm'))) {
            e.preventDefault();
            return false;
        }
    });

    // 自动收起的摘要（鼠标悬停效果）
    $('.paper-card').on('mouseenter', function() {
        $(this).find('.abstract-text').css('background-color', '#e9ecef');
    }).on('mouseleave', function() {
        $(this).find('.abstract-text').css('background-color', '#f8f9fa');
    });

    // 平滑滚动
    $('a[href^="#"]').on('click', function(e) {
        const target = $(this.getAttribute('href'));
        if (target.length) {
            e.preventDefault();
            $('html, body').stop().animate({
                scrollTop: target.offset().top - 70
            }, 300);
        }
    });

    // 搜索框自动完成（可扩展）
    const searchInput = $('input[name="q"]');
    if (searchInput.length) {
        searchInput.attr('autocomplete', 'off');
    }

    // 检测 URL 参数并应用过滤
    const urlParams = new URLSearchParams(window.location.search);

    // 设置 domain 选择器
    const domainParam = urlParams.get('domain');
    if (domainParam) {
        $('#domainFilter').val(domainParam);
    }

    // 设置年份选择器
    const yearParam = urlParams.get('year');
    if (yearParam) {
        $('#yearFilter').val(yearParam);
    }

    // 设置数据源复选框
    const sourcesParam = urlParams.get('sources');
    if (sourcesParam) {
        const sources = sourcesParam.split(',');
        $('#sourceArxiv').prop('checked', sources.includes('arxiv'));
        $('#sourceDblp').prop('checked', sources.includes('dblp'));
    }

    loadReadPaperState();
});

// ============== 已读论文状态 ==============
const READ_PAPERS_STORAGE_KEY = 'paperinfo_read_paper_ids';
let readPaperIds = new Set();

function loadReadPaperState() {
    try {
        const savedIds = JSON.parse(localStorage.getItem(READ_PAPERS_STORAGE_KEY) || '[]');
        readPaperIds = new Set(savedIds.map(String));
    } catch (err) {
        console.warn('恢复已读论文状态失败:', err);
        readPaperIds = new Set();
    }

    document.querySelectorAll('.paper-card[data-id]').forEach(card => {
        if (readPaperIds.has(String(card.dataset.id))) {
            card.dataset.read = 'true';
            card.querySelector('.paper-title-link')?.classList.add('paper-title-read');
        }
    });
}

function markPaperRead(paperId) {
    const id = String(paperId);
    readPaperIds.add(id);
    try {
        localStorage.setItem(READ_PAPERS_STORAGE_KEY, JSON.stringify([...readPaperIds]));
    } catch (err) {
        console.warn('保存已读论文状态失败:', err);
    }

    const card = document.querySelector(`.paper-card[data-id="${id}"]`);
    if (card) {
        card.dataset.read = 'true';
        card.querySelector('.paper-title-link')?.classList.add('paper-title-read');
    }

    // 上报服务端，用于"历史-最近点击"（失败不影响交互）
    fetch(`/api/papers/${id}/read`, { method: 'POST' }).catch(() => {});
}

/**
 * 显示 Toast 通知
 */
function showToast(message, type = 'info') {
    const toastContainer = $('#toast-container');
    if (!toastContainer.length) {
        $('body').append('<div id="toast-container" class="position-fixed top-0 end-0 p-3"></div>');
    }

    const bgClass = {
        'success': 'bg-success',
        'error': 'bg-danger',
        'warning': 'bg-warning',
        'info': 'bg-info'
    }[type] || 'bg-info';

    const toastHtml = `
        <div class="toast align-items-center text-white ${bgClass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;

    const toast = $(toastHtml);
    $('#toast-container').append(toast);

    const bsToast = new bootstrap.Toast(toast[0], { delay: 3000 });
    bsToast.show();

    toast.on('hidden.bs.toast', function() {
        $(this).remove();
    });
}

/**
 * 复制文本到剪贴板
 */
function copyToClipboard(text, buttonElement) {
    navigator.clipboard.writeText(text).then(function() {
        const originalText = $(buttonElement).html();
        $(buttonElement).html('<i class="bi bi-check"></i>');
        setTimeout(function() {
            $(buttonElement).html(originalText);
        }, 2000);
        showToast('已复制到剪贴板', 'success');
    }).catch(function(err) {
        showToast('复制失败', 'error');
    });
}

/**
 * 通过服务端接口导出论文并触发下载
 * @param {string} format - 'csv' 或 'bibtex'
 * @param {object} payload - 选择范围，如 {paper_ids: [...]} / {select_all: true, filters: {...}} / {favorites: true}
 */
async function downloadExport(format, payload) {
    try {
        const res = await fetch(`/api/export/${format}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const d = await res.json().catch(() => ({}));
            showToast(d.message || '导出失败', 'error');
            return;
        }
        const blob = await res.blob();
        const dateStr = new Date().toISOString().split('T')[0];
        const ext = { csv: 'csv', bibtex: 'bib', xlsx: 'xlsx' }[format] || format;
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `papers_${dateStr}.${ext}`;
        link.click();
        URL.revokeObjectURL(link.href);
        showToast('导出成功', 'success');
    } catch (err) {
        showToast('导出失败: ' + err.message, 'error');
    }
}

/**
 * 格式化日期
 */
function formatDate(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    });
}

/**
 * 防抖函数
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * 搜索框输入防抖（可用于实现即时搜索）
 */
const searchInput = $('input[name="q"]');
if (searchInput.length) {
    const debouncedSearch = debounce(function() {
        // 这里可以实现 AJAX 搜索
        console.log('Searching for:', searchInput.val());
    }, 500);

    searchInput.on('input', debouncedSearch);
}

// ============== 翻译功能（公共函数） ==============

/**
 * 翻译文本（支持分段）
 * @param {string} text - 要翻译的文本
 * @returns {Promise<string>} 翻译结果
 */
async function translateText(text) {
    const MAX_LENGTH = 3000;
    const textToTranslate = text.trim();

    if (!textToTranslate) {
        throw new Error('待翻译文本不能为空');
    }

    if (textToTranslate.length <= MAX_LENGTH) {
        return await doTranslate(textToTranslate);
    }

    const chunks = splitText(textToTranslate, MAX_LENGTH);
    const translatedChunks = [];

    for (let i = 0; i < chunks.length; i++) {
        try {
            const translated = await doTranslate(chunks[i]);
            translatedChunks.push(translated);
        } catch (err) {
            console.error(`翻译第 ${i + 1} 段失败:`, err);
            throw err;
        }
    }

    return translatedChunks.join('');
}

/**
 * 执行翻译API调用
 * @param {string} text - 要翻译的文本
 * @returns {Promise<string>} 翻译结果
 */
async function doTranslate(text) {
    const response = await fetch('/api/translate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.success || !data.translated_text) {
        throw new Error(data.message || `翻译请求失败（HTTP ${response.status}）`);
    }
    console.debug(`翻译服务提供方: ${data.provider}`);
    return data.translated_text;
}

/**
 * 分割文本为指定长度的块
 * @param {string} text - 要分割的文本
 * @param {number} maxLength - 最大长度
 * @returns {string[]} 分割后的文本数组
 */
function splitText(text, maxLength) {
    const chunks = [];
    const sentences = text.split(/(?<=[.!?。！？])\s+/);
    let currentChunk = '';

    for (const sentence of sentences) {
        if ((currentChunk + sentence).length <= maxLength) {
            currentChunk += (currentChunk ? ' ' : '') + sentence;
        } else {
            if (currentChunk) {
                chunks.push(currentChunk.trim());
            }
            currentChunk = sentence;
        }
    }

    if (currentChunk) {
        chunks.push(currentChunk.trim());
    }

    return chunks;
}

// ============== localStorage 管理（公共函数） ==============

/**
 * 从 localStorage 加载翻译结果
 * @param {Object} translations - 翻译存储对象 {titles: {}, abstracts: {}}
 * @param {string} titlesKey - 标题翻译的 localStorage 键
 * @param {string} abstractsKey - 摘要翻译的 localStorage 键
 */
function loadTranslationsFromStorage(translations, titlesKey, abstractsKey) {
    try {
        const savedTitles = localStorage.getItem(titlesKey);
        const savedAbstracts = localStorage.getItem(abstractsKey);

        if (savedTitles) {
            translations.titles = JSON.parse(savedTitles);
            console.log('已恢复标题翻译:', Object.keys(translations.titles).length, '篇');
        }
        if (savedAbstracts) {
            translations.abstracts = JSON.parse(savedAbstracts);
            console.log('已恢复摘要翻译:', Object.keys(translations.abstracts).length, '篇');
        }
    } catch (err) {
        console.error('恢复翻译结果失败:', err);
    }
}

/**
 * 保存翻译结果到 localStorage
 * @param {Object} translations - 翻译存储对象 {titles: {}, abstracts: {}}
 * @param {string} titlesKey - 标题翻译的 localStorage 键
 * @param {string} abstractsKey - 摘要翻译的 localStorage 键
 */
function saveTranslationsToStorage(translations, titlesKey, abstractsKey) {
    try {
        localStorage.setItem(titlesKey, JSON.stringify(translations.titles));
        localStorage.setItem(abstractsKey, JSON.stringify(translations.abstracts));
    } catch (err) {
        console.error('保存翻译结果失败:', err);
    }
}

// ============== 搜索历史记录功能 ==============

const SEARCH_HISTORY_KEY = 'paperinfo_search_history';
const MAX_HISTORY_ITEMS = 10;

/**
 * 加载搜索历史记录
 */
function loadSearchHistory() {
    try {
        const history = localStorage.getItem(SEARCH_HISTORY_KEY);
        return history ? JSON.parse(history) : [];
    } catch (err) {
        console.error('加载搜索历史失败:', err);
        return [];
    }
}

/**
 * 保存搜索历史记录
 */
function saveSearchHistory(history) {
    try {
        localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(history));
    } catch (err) {
        console.error('保存搜索历史失败:', err);
    }
}

/**
 * 添加搜索关键词到历史记录
 */
function addSearchHistory(keyword) {
    if (!keyword || keyword.trim() === '') return;

    keyword = keyword.trim();
    let history = loadSearchHistory();

    // 移除重复项
    history = history.filter(item => item !== keyword);

    // 添加到开头
    history.unshift(keyword);

    // 限制数量
    if (history.length > MAX_HISTORY_ITEMS) {
        history = history.slice(0, MAX_HISTORY_ITEMS);
    }

    saveSearchHistory(history);
    renderSearchHistory();
}

/**
 * 清空搜索历史
 */
function clearSearchHistory() {
    localStorage.removeItem(SEARCH_HISTORY_KEY);
    renderSearchHistory();
    showToast('搜索历史已清空', 'info');
}

/**
 * 渲染搜索历史记录
 */
function renderSearchHistory() {
    const history = loadSearchHistory();
    const historyList = document.getElementById('searchHistory');
    if (!historyList) return;

    // 保留标题和分隔线
    historyList.innerHTML = `
        <li><h6 class="dropdown-header">搜索历史 <span id="clearHistory" style="cursor:pointer;float:right;" title="清空历史"><i class="bi bi-x-circle"></i></span></h6></li>
        <li><hr class="dropdown-divider"></li>
    `;

    if (history.length === 0) {
        const emptyItem = document.createElement('li');
        emptyItem.innerHTML = '<span class="dropdown-item text-muted">暂无历史记录</span>';
        emptyItem.style.cursor = 'default';
        historyList.appendChild(emptyItem);
    } else {
        history.forEach(keyword => {
            const item = document.createElement('li');
            const link = document.createElement('a');
            link.className = 'dropdown-item';
            link.href = `/?q=${encodeURIComponent(keyword)}`;
            link.textContent = keyword;
            item.appendChild(link);
            historyList.appendChild(item);
        });
    }
}

/**
 * 初始化搜索历史功能
 */
function initSearchHistory() {
    const searchInput = document.getElementById('searchInput');
    const clearHistoryBtn = document.getElementById('clearHistory');
    const searchForm = document.getElementById('searchForm');

    if (!searchInput) return;

    // 渲染历史记录
    renderSearchHistory();

    // 聚焦时显示历史
    searchInput.addEventListener('focus', function() {
        renderSearchHistory();
    });

    // 输入时过滤历史
    searchInput.addEventListener('input', function() {
        const keyword = this.value.toLowerCase();
        const history = loadSearchHistory();
        const historyList = document.getElementById('searchHistory');

        if (historyList) {
            const items = historyList.querySelectorAll('a.dropdown-item');
            items.forEach(item => {
                const text = item.textContent.toLowerCase();
                item.style.display = text.includes(keyword) ? '' : 'none';
            });
        }
    });

    // 表单提交时保存历史
    if (searchForm) {
        searchForm.addEventListener('submit', function(e) {
            const keyword = searchInput.value;
            if (keyword) {
                addSearchHistory(keyword);
            }
        });
    }

    // 清空历史
    if (clearHistoryBtn) {
        clearHistoryBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            clearSearchHistory();
        });
    }
}

// 页面加载时初始化搜索历史
$(document).ready(function() {
    initSearchHistory();
});
