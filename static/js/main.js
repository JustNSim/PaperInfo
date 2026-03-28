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
});

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
 * 导出论文列表为 CSV
 */
function exportToCSV() {
    const papers = [];
    $('.paper-card').each(function() {
        const $card = $(this);
        papers.push({
            title: $card.find('.card-title').text().trim(),
            authors: $card.find('.text-muted').text().replace(/^[^\u4e00-\u9fa5]*/, '').trim(),
            source: $card.find('.badge').first().text().trim(),
            year: $card.find('.badge.bg-info').text().trim(),
            url: $card.find('a[href]').first().attr('href')
        });
    });

    if (papers.length === 0) {
        showToast('没有可导出的论文', 'warning');
        return;
    }

    // 生成 CSV 内容
    const headers = ['Title', 'Authors', 'Source', 'Year', 'URL'];
    const csvContent = [
        headers.join(','),
        ...papers.map(p => [
            `"${p.title.replace(/"/g, '""')}"`,
            `"${p.authors.replace(/"/g, '""')}"`,
            p.source,
            p.year,
            p.url
        ].join(','))
    ].join('\n');

    // 下载文件
    const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `papers_${new Date().toISOString().split('T')[0]}.csv`;
    link.click();

    showToast('CSV 文件已下载', 'success');
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
    const MAX_LENGTH = 500;
    const textToTranslate = text.trim();

    if (textToTranslate.length <= MAX_LENGTH) {
        return await doTranslate(textToTranslate);
    }

    const chunks = splitText(textToTranslate, MAX_LENGTH);
    const translatedChunks = [];

    for (let i = 0; i < chunks.length; i++) {
        try {
            const translated = await doTranslate(chunks[i]);
            translatedChunks.push(translated);
            if (i < chunks.length - 1) {
                await new Promise(resolve => setTimeout(resolve, 300));
            }
        } catch (err) {
            console.error(`翻译第 ${i + 1} 段失败:`, err);
            translatedChunks.push(chunks[i]);
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
    try {
        const url = `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text)}&langpair=en|zh-CN`;
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        const data = await response.json();
        if (data.responseStatus === 200 && data.responseData && data.responseData.translatedText) {
            return data.responseData.translatedText;
        }
        if (data.responseStatus === 403 || data.responseStatus === 429) {
            return await fallbackTranslate(text);
        }
        throw new Error(data.responseDetails || 'API 返回错误');
    } catch (err) {
        console.error('翻译请求失败:', err);
        return await fallbackTranslate(text);
    }
}

/**
 * 备用翻译服务（LibreTranslate）
 * @param {string} text - 要翻译的文本
 * @returns {Promise<string>} 翻译结果
 */
async function fallbackTranslate(text) {
    try {
        const url = 'https://libretranslate.com/translate';
        const response = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                q: text,
                source: 'en',
                target: 'zh',
                format: 'text'
            })
        });
        if (!response.ok) {
            throw new Error(`LibreTranslate HTTP ${response.status}`);
        }
        const data = await response.json();
        return data.translatedText;
    } catch (err) {
        console.error('备用翻译也失败:', err);
        throw new Error('翻译服务暂时不可用，请稍后重试');
    }
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
