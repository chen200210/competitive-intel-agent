from pathlib import Path
from docx import Document
p = Path(r"E:\DOSH\OA\zhaochen_cv_zh_agent_rag_clean.docx")
doc = Document(p)
for i, para in enumerate(doc.paragraphs):
    t = para.text.strip()
    if t:
        print(i, repr(t))
