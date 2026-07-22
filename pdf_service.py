"""Shared, rate-limited PDF downloading for ZIP and Zotero exports."""
import logging
import re
import time
import zipfile

import requests

from config import Config


logger = logging.getLogger(__name__)


class PDFDownloadError(RuntimeError):
    """Raised when a remote PDF cannot be downloaded or validated."""


class PDFDownloader:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._last_download_at = 0.0

    def close(self):
        if self._owns_session:
            self.session.close()

    def download(self, url):
        if not str(url or '').lower().startswith(('http://', 'https://')):
            raise PDFDownloadError('PDF 地址不是有效的 HTTP(S) URL')

        elapsed = time.monotonic() - self._last_download_at
        if elapsed < Config.PDF_DOWNLOAD_DELAY:
            time.sleep(Config.PDF_DOWNLOAD_DELAY - elapsed)
        self._last_download_at = time.monotonic()

        try:
            response = self.session.get(
                url,
                headers={'User-Agent': 'PaperInfo/1.0 (PDF export)'},
                timeout=Config.PDF_DOWNLOAD_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise PDFDownloadError(f'PDF 下载失败：{exc}') from exc

        max_bytes = Config.PDF_MAX_MB * 1024 * 1024
        try:
            declared_size = int(response.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > max_bytes:
            raise PDFDownloadError(f'PDF 超过 {Config.PDF_MAX_MB} MB 大小限制')

        content = response.content
        if len(content) > max_bytes:
            raise PDFDownloadError(f'PDF 超过 {Config.PDF_MAX_MB} MB 大小限制')
        if b'%PDF-' not in content[:1024]:
            raise PDFDownloadError('下载内容不是有效的 PDF')
        return content


def _safe_pdf_title(paper):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', paper.title or 'paper')
    title = re.sub(r'\s+', ' ', title).strip(' ._') or 'paper'
    # Leave room for the numeric prefix, paper ID and extension on Windows.
    return title[:120].rstrip(' .')


def safe_pdf_filename(paper, index):
    title = _safe_pdf_title(paper)
    return f'{index:03d}_{title}_{paper.id}.pdf'


def safe_single_pdf_filename(paper):
    return f'{_safe_pdf_title(paper)}_{paper.id}.pdf'


def write_papers_pdf_zip(papers, output, downloader):
    """Download available PDFs into an uncompressed ZIP and return counts."""
    result = {'pdf_saved': 0, 'pdf_failed': 0, 'pdf_skipped': 0, 'failures': []}
    with zipfile.ZipFile(output, mode='w', compression=zipfile.ZIP_STORED) as archive:
        for index, paper in enumerate(papers, start=1):
            if not paper.pdf_url:
                result['pdf_skipped'] += 1
                continue
            try:
                content = downloader.download(paper.pdf_url)
                archive.writestr(safe_pdf_filename(paper, index), content)
                result['pdf_saved'] += 1
            except PDFDownloadError as exc:
                result['pdf_failed'] += 1
                result['failures'].append({
                    'id': paper.id,
                    'title': paper.title,
                    'reason': str(exc),
                })
                logger.warning(
                    '批量 PDF 下载失败 paper_id=%s url=%s: %s',
                    paper.id, paper.pdf_url, exc,
                )

        if result['pdf_failed'] or result['pdf_skipped']:
            lines = [
                'PaperInfo PDF 下载报告',
                f'成功: {result["pdf_saved"]}',
                f'无 PDF 地址: {result["pdf_skipped"]}',
                f'下载失败: {result["pdf_failed"]}',
            ]
            if result['failures']:
                lines.extend(['', '失败明细:'])
                lines.extend(
                    f'- [{failure["id"]}] {failure["title"]}: {failure["reason"]}'
                    for failure in result['failures']
                )
            archive.writestr('PaperInfo_PDF_下载报告.txt', '\n'.join(lines))
    return result
