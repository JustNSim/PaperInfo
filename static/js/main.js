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
