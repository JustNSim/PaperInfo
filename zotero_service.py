"""Write PaperInfo records to a running Zotero desktop client."""
import json
import logging
import re
import uuid

import requests

from config import Config
from pdf_service import PDFDownloader, PDFDownloadError


logger = logging.getLogger(__name__)


class ZoteroLocalError(RuntimeError):
    """Raised when the local Zotero connector cannot accept an export."""


def _split_author(name):
    value = str(name or '').strip()
    if not value:
        return None
    if ',' in value:
        last_name, first_name = (part.strip() for part in value.split(',', 1))
    else:
        parts = value.split()
        if len(parts) == 1:
            first_name, last_name = '', parts[0]
        else:
            first_name, last_name = ' '.join(parts[:-1]), parts[-1]
    return {
        'creatorType': 'author',
        'firstName': first_name,
        'lastName': last_name,
    }


def _arxiv_id(paper):
    value = str(paper.source_id or '').strip().split('/')[-1]
    return re.sub(r'v\d+$', '', value, flags=re.IGNORECASE)


def paper_to_zotero_item(paper):
    """Map a Paper model to the metadata accepted by /connector/saveItems."""
    creators = [creator for creator in (
        _split_author(author) for author in (paper.authors or [])
    ) if creator]
    date = ''
    if paper.published_date:
        date = paper.published_date.strftime('%Y-%m-%d')
    elif paper.year:
        date = str(paper.year)

    doi = paper.doi or ''
    if not doi and paper.source == 'arxiv' and paper.source_id:
        doi = f'10.48550/arXiv.{_arxiv_id(paper)}'

    extra_lines = [f'PaperInfo ID: {paper.id}']
    if paper.affiliation_names:
        extra_lines.append('Affiliations: ' + '; '.join(paper.affiliation_names))

    item = {
        'itemType': 'preprint' if paper.source == 'arxiv' else 'conferencePaper',
        'title': paper.title,
        'creators': creators,
        'abstractNote': paper.abstract or '',
        'date': date,
        'DOI': doi,
        'url': paper.url or paper.pdf_url or '',
        'libraryCatalog': 'PaperInfo',
        'extra': '\n'.join(extra_lines),
        'tags': [
            {'tag': 'PaperInfo'},
            *([{'tag': paper.domain.name}] if paper.domain else []),
        ],
    }
    if paper.source == 'arxiv':
        item.update({
            'repository': 'arXiv',
            'archiveID': f'arXiv:{_arxiv_id(paper)}',
        })
    elif paper.venue:
        item.update({
            'proceedingsTitle': paper.venue,
            'conferenceName': paper.venue,
        })
    return item


class ZoteroLocalClient:
    """Minimal client for Zotero's local Connector HTTP server."""

    def __init__(self, base_url=None, timeout=None, session=None):
        self.base_url = (base_url or Config.ZOTERO_LOCAL_URL).rstrip('/')
        self.timeout = timeout or Config.ZOTERO_LOCAL_TIMEOUT
        self.session = session or requests.Session()
        self.headers = {
            'Content-Type': 'application/json',
            'X-Zotero-Connector-API-Version': '3',
        }
        self.pdf_downloader = PDFDownloader(session=self.session)

    def close(self):
        self.session.close()

    def ping(self):
        try:
            response = self.session.get(
                f'{self.base_url}/connector/ping',
                headers={'X-Zotero-Connector-API-Version': '3'},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.headers.get('X-Zotero-Version')
        except requests.RequestException as exc:
            raise ZoteroLocalError(
                '无法连接 Zotero，请确认 Zotero 桌面端正在运行'
            ) from exc

    def get_save_targets(self):
        """Return editable Zotero libraries/collections and the active target."""
        try:
            response = self.session.post(
                f'{self.base_url}/connector/getSelectedCollection',
                headers=self.headers,
                json={'switchToReadableLibrary': True},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise ZoteroLocalError(
                    f'读取 Zotero 分类失败（HTTP {response.status_code}）'
                )
            data = response.json()
        except requests.RequestException as exc:
            raise ZoteroLocalError(
                '无法读取 Zotero 分类，请确认 Zotero 桌面端正在运行'
            ) from exc
        except ValueError as exc:
            raise ZoteroLocalError('Zotero 返回了无效的分类数据') from exc

        targets = [
            {
                'id': str(target.get('id') or ''),
                'name': str(target.get('name') or ''),
                'level': int(target.get('level') or 0),
                'files_editable': bool(target.get('filesEditable', True)),
            }
            for target in (data.get('targets') or [])
            if target.get('id') and target.get('name')
        ]
        selected_target = (
            f'C{data["id"]}' if data.get('id') is not None
            else f'L{data["libraryID"]}'
        )
        return {
            'targets': targets,
            'selected_target': selected_target,
            'library_name': data.get('libraryName'),
        }

    def _update_session_target(self, session_id, target_id):
        try:
            response = self.session.post(
                f'{self.base_url}/connector/updateSession',
                headers=self.headers,
                json={'sessionID': session_id, 'target': target_id},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                detail = (response.text or '').strip()
                raise ZoteroLocalError(
                    f'移动到 Zotero 分类失败（HTTP {response.status_code}）'
                    + (f'：{detail[:200]}' if detail else '')
                )
        except requests.RequestException as exc:
            raise ZoteroLocalError(f'移动到 Zotero 分类失败：{exc}') from exc

    def _save_pdf_attachment(self, pdf_url, session_id, parent_item_id):
        try:
            content = self.pdf_downloader.download(pdf_url)
        except PDFDownloadError as exc:
            raise ZoteroLocalError(str(exc)) from exc
        metadata = {
            'sessionID': session_id,
            'parentItemID': parent_item_id,
            'title': 'Full Text PDF',
            'url': pdf_url,
        }
        try:
            response = self.session.post(
                f'{self.base_url}/connector/saveAttachment',
                headers={
                    'Content-Type': 'application/pdf',
                    'Content-Length': str(len(content)),
                    'X-Metadata': json.dumps(metadata),
                    'X-Zotero-Connector-API-Version': '3',
                },
                data=content,
                timeout=self.timeout,
            )
            if response.status_code not in (200, 201, 204):
                detail = (response.text or '').strip()
                raise ZoteroLocalError(
                    f'PDF 写入 Zotero 失败（HTTP {response.status_code}）'
                    + (f'：{detail[:200]}' if detail else '')
                )
        except requests.RequestException as exc:
            raise ZoteroLocalError(f'PDF 写入 Zotero 失败：{exc}') from exc

    def save_papers(self, papers, batch_size=50, target_id=None):
        papers = list(papers or [])
        if not papers:
            return {
                'saved': 0, 'batches': 0, 'pdf_saved': 0,
                'pdf_failed': 0, 'pdf_skipped': 0, 'zotero_version': None,
            }
        version = self.ping()
        target_name = None
        if target_id:
            target_info = self.get_save_targets()
            target_map = {
                target['id']: target for target in target_info['targets']
            }
            if target_id not in target_map:
                raise ZoteroLocalError('选择的 Zotero 分类不存在或不可写')
            target_name = target_map[target_id]['name']
        items = [paper_to_zotero_item(paper) for paper in papers]
        batches = 0
        pdf_saved = 0
        pdf_failed = 0
        pdf_skipped = 0
        for offset in range(0, len(items), batch_size):
            chunk = items[offset:offset + batch_size]
            paper_chunk = papers[offset:offset + batch_size]
            item_ids = []
            for item in chunk:
                item_id = f'paperinfo-{uuid.uuid4().hex}'
                item['id'] = item_id
                item_ids.append(item_id)
            source_paper = papers[offset]
            session_id = f'paperinfo-{uuid.uuid4().hex}'
            payload = {
                'items': chunk,
                'uri': source_paper.url or 'http://127.0.0.1:5000/',
                'sessionID': session_id,
            }
            try:
                response = self.session.post(
                    f'{self.base_url}/connector/saveItems',
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code not in (200, 201, 204):
                    detail = (response.text or '').strip()
                    raise ZoteroLocalError(
                        f'Zotero 写入失败（HTTP {response.status_code}）'
                        + (f'：{detail[:200]}' if detail else '')
                    )
            except requests.RequestException as exc:
                raise ZoteroLocalError(f'连接 Zotero 写入失败：{exc}') from exc
            batches += 1

            # The Connector links attachments by the custom item ID and the
            # same save session used for the parent metadata items.
            for paper, item_id in zip(paper_chunk, item_ids):
                if not paper.pdf_url:
                    pdf_skipped += 1
                    continue
                try:
                    self._save_pdf_attachment(paper.pdf_url, session_id, item_id)
                    pdf_saved += 1
                except ZoteroLocalError as exc:
                    # Keep the successfully created metadata item and report
                    # the attachment failure in the final summary.
                    pdf_failed += 1
                    logger.warning(
                        'Zotero PDF 附件保存失败 paper_id=%s url=%s: %s',
                        paper.id, paper.pdf_url, exc,
                    )
            if target_id:
                self._update_session_target(session_id, target_id)
        return {
            'saved': len(items),
            'batches': batches,
            'pdf_saved': pdf_saved,
            'pdf_failed': pdf_failed,
            'pdf_skipped': pdf_skipped,
            'zotero_version': version,
            'target_id': target_id,
            'target_name': target_name,
        }
