import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="bsi-test-")
os.environ["BSI_DATA_DIR"] = _tmp
os.environ["BSI_DATABASE_URL"] = f"sqlite:///{_tmp}/bsi.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from bsi import db  # noqa: E402

db.init()


@pytest.fixture
def make_pdf(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    def _make(name, pages):
        p = tmp_path / name
        c = canvas.Canvas(str(p), pagesize=letter)
        for text in pages:
            y = 720
            for line in text.split("\n"):
                c.drawString(72, y, line)
                y -= 16
            c.showPage()
        c.save()
        return p
    return _make
