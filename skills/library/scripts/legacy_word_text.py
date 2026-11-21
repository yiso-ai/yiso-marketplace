"""Read the text of a binary Word document with an isolated, headless LibreOffice."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import local_sources


def extract_doc(path):
    program = "soffice.com" if os.name == "nt" else "soffice"
    executable = shutil.which(program)
    if not executable:
        raise ValueError("读取旧式 .doc 正文需要本机 LibreOffice")
    source_hash, _ = local_sources.read_source(path)
    with tempfile.TemporaryDirectory(prefix="library-doc-profile-") as tmp:
        profile = Path(tmp)
        settings = profile / "user" / "registrymodifications.xcu"
        settings.parent.mkdir()
        settings.write_text(
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
            '</item></oor:items>', encoding="utf-8")
        try:
            result = subprocess.run(
                [executable, "-env:UserInstallation=" + profile.resolve().as_uri(),
                 "--headless", "--norestore", "--nologo", "--cat", str(path)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=90, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("旧式 Word 文本读取超时，请核对原件") from exc
    if result.returncode or not result.stdout.strip():
        raise ValueError("旧式 Word 正文未能读取，请核对是否加密或缺少可提取文本")
    if local_sources.read_source(path)[0] != source_hash:
        raise ValueError("读取过程中 Word 原件已更改")
    body = result.stdout.decode("utf-8-sig").replace("\r\n", "\n").rstrip("\n")
    if not body.strip() or "\x00" in body:
        raise ValueError("旧式 Word 正文为空或无效")
    return {"path": str(path), "source_sha256": source_hash, "body": body,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "native_tables": [], "supplemental_parts": [],
            "supplemental_hashes": {"legacy-binary:visual-review": source_hash},
            "limitations": ["旧式 .doc 只提取文字；图表、脚注、修订等需查看原件。二进制变化本身不能证明有新增研究内容。"]}
