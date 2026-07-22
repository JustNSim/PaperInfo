import io
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

from pdf_service import (
    PDFDownloader,
    safe_pdf_filename,
    safe_single_pdf_filename,
    write_papers_pdf_zip,
)


class PDFServiceTests(unittest.TestCase):
    def test_downloader_validates_pdf_content(self):
        response = Mock()
        response.headers = {'Content-Length': '18'}
        response.content = b'%PDF-1.7 test data'
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response

        content = PDFDownloader(session=session).download('https://example.test/a.pdf')

        self.assertEqual(b'%PDF-1.7 test data', content)

    def test_zip_contains_pdfs_and_failure_report(self):
        papers = [
            SimpleNamespace(id=1, title='A / Paper', pdf_url='https://example.test/a.pdf'),
            SimpleNamespace(id=2, title='No PDF', pdf_url=None),
        ]
        downloader = Mock()
        downloader.download.return_value = b'%PDF-1.7 test data'
        output = io.BytesIO()

        result = write_papers_pdf_zip(papers, output, downloader)

        self.assertEqual(1, result['pdf_saved'])
        self.assertEqual(1, result['pdf_skipped'])
        output.seek(0)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertIn('001_A _ Paper_1.pdf', names)
            self.assertIn('PaperInfo_PDF_下载报告.txt', names)

    def test_filename_removes_windows_reserved_characters(self):
        paper = SimpleNamespace(id=9, title='A:B*C?D', pdf_url='x')
        self.assertEqual('003_A_B_C_D_9.pdf', safe_pdf_filename(paper, 3))
        self.assertEqual('A_B_C_D_9.pdf', safe_single_pdf_filename(paper))


if __name__ == '__main__':
    unittest.main()
