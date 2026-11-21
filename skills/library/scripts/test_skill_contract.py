"""Offline packaging checks; not permission, semantic, or production certification."""
import re, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class SkillContractTests(unittest.TestCase):
    def test_relative_document_links(self):
        for file in [ROOT/'SKILL.md', *sorted((ROOT/'references').glob('*.md'))]:
            for target in re.findall(r'\]\(([^)]+)\)',file.read_text()):
                if not target.startswith(('http:','https:','#')):
                    self.assertTrue((file.parent/target.split('#')[0]).exists(),str(file)+': '+target)
    def test_no_deployment_identity_or_credentials(self):
        patterns=[r'appgprj_[a-z0-9]{16,}',r'libfile_[a-z0-9_-]{20,}',r'file_[a-f0-9]{24,}',r'Bearer [A-Za-z0-9_-]{20,}',r'-----BEGIN [A-Z ]*PRIVATE KEY']
        for folder in ['references','assets']:
            for file in (ROOT/folder).rglob('*'):
                if not file.is_file() or '__pycache__' in str(file) or 'dist' in file.parts: continue
                text=file.read_text()
                for pattern in patterns:self.assertIsNone(re.search(pattern,text),str(file))
    def test_current_editorial_contract(self):
        text=(ROOT/'references/editorial.md').read_text()
        self.assertIn('display_author',text)
        self.assertIn('原生表格',text)
        self.assertNotIn('内部研究元数据仅日期',text)
        self.assertNotIn('内部作者仅私有证据保留，前端不显示',text)
        self.assertIn('关键图表/图片表格',(ROOT/'SKILL.md').read_text())
        self.assertNotIn('不归档图片',(ROOT/'SKILL.md').read_text())
        images=(ROOT/'references/images.md').read_text()
        for required in ['旧档不回补','联合备份','SHA256','原生表格','未实现']:
            self.assertIn(required,images)

if __name__=='__main__':unittest.main()
