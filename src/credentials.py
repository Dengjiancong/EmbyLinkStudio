"""Read an explicitly chosen token file; never evaluate environment expressions."""
from pathlib import Path
import re
def read_token_file(filename):
    path=Path(filename)
    try:
        with path.open("rb") as stream:raw=stream.read(8193)
        if len(raw)>8192:raise ValueError()
        text=raw.decode("utf-8-sig").strip()
        lines=[line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        assignments=[m.group(1).strip() for line in lines if (m:=re.match(r"^(?:export\s+)?AGENT_TOKEN\s*=\s*(.*)$",line))]
        if assignments:
            if len(assignments)!=1:raise ValueError()
            token=assignments[0]
            if len(token)>=2 and token[0]==token[-1] and token[0] in ("'",'"'):token=token[1:-1]
        elif len(lines)==1 and ("=" not in lines[0] or re.fullmatch(r"[A-Za-z0-9+/_-]{24,}={1,2}",lines[0])):token=lines[0]
        else:raise ValueError()
        if not 24<=len(token)<=512 or not token.isascii() or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in token):raise ValueError()
        return token
    except (OSError,ValueError,UnicodeError):
        raise ValueError("无法读取密钥文件。请选择 UTF-8 文件：单行密钥，或包含唯一 AGENT_TOKEN=值 的 .env；密钥需24～512个可打印ASCII字符。") from None
