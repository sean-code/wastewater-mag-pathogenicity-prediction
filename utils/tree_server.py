#!/usr/bin/env python3
"""
tree_server.py -- live, dynamic HTML file-tree browser for large HPC directories.

Unlike a static snapshot, this reads the filesystem ON DEMAND (one directory per
click), so it stays current, scales to millions of files, and never pre-scans.
Folder sizes are computed only when you click the (Sigma) button. Pure stdlib --
no pip installs, HPC-friendly.

RUN (on the cluster):
    python tree_server.py --root /path/to/scratch --port 8765
    # default --root is the current directory; --host defaults to 127.0.0.1

VIEW (from your laptop) -- compute nodes aren't directly reachable, so tunnel:
    ssh -L 8765:localhost:8765 john.kangethe@<login-node>
    # (or -L 8765:<compute-node>:8765 via the login node)
    then open http://localhost:8765

SECURITY: binds to 127.0.0.1 by default (only reachable through your SSH tunnel).
Browsing is confined at/below --root (".." can't climb above it). Use --root /
to browse the whole filesystem. Do NOT bind --host 0.0.0.0 on a shared cluster.
"""
import os, sys, json, argparse, urllib.parse, stat as statmod
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- file-type classification (kind codes shared with the viewer JS) ----
_KIND_EXT = [
    (1, (".fa", ".fasta", ".fna", ".faa", ".ffn", ".fastq", ".fq", ".gz",
         ".fa.gz", ".fasta.gz", ".fna.gz", ".fastq.gz", ".fq.gz", ".gbff", ".sam", ".bam")),
    (2, (".zip", ".tar", ".tgz", ".tar.gz", ".bz2", ".xz", ".7z")),
    (3, (".csv", ".tsv", ".json", ".jsonl", ".parquet", ".h5", ".hdf5")),
    (4, (".docx", ".doc", ".pdf", ".txt", ".md", ".rtf")),
    (5, (".ipynb",)),
    (6, (".py", ".sh", ".r", ".pl", ".js", ".c", ".cpp", ".java")),
    (7, (".log", ".err", ".out")),
    (8, (".xml",)),
]
def kind_of(name):
    low = name.lower()
    for code, exts in _KIND_EXT:
        for e in exts:
            if low.endswith(e):
                return code
    return 0

ROOT = os.getcwd()
SHOW_HIDDEN = False

def resolve(relpath):
    """Map a '/'-separated path (relative to ROOT) to an absolute path, never
    allowing '..' to climb above ROOT. Symlinks under ROOT are followed (so you
    can navigate through them), but you can't lexically escape the root."""
    parts = []
    for p in (relpath or "").split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            continue
        parts.append(p)
    return os.path.join(ROOT, *parts), "/".join(parts)

def list_dir(relpath, offset, limit):
    abs_path, norm = resolve(relpath)
    out = {"path": norm, "abs": abs_path, "readable": True, "entries": [],
           "total": 0, "offset": offset, "limit": limit}
    try:
        with os.scandir(abs_path) as it:
            raw = list(it)
    except PermissionError:
        out["readable"] = False; return out
    except (FileNotFoundError, NotADirectoryError, OSError) as e:
        out["readable"] = False; out["error"] = str(e); return out
    rows = []
    for e in raw:
        if not SHOW_HIDDEN and e.name.startswith("."):
            continue
        try:
            is_dir = e.is_dir(follow_symlinks=True)
        except OSError:
            is_dir = False
        is_link = e.is_symlink()
        size = None
        mtime = 0
        try:
            st = e.stat(follow_symlinks=True)
            mtime = int(st.st_mtime)
            if not is_dir:
                size = st.st_size
        except OSError:
            pass
        rows.append({
            "name": e.name, "dir": is_dir, "size": size, "mtime": mtime,
            "kind": 0 if is_dir else kind_of(e.name), "link": is_link,
        })
    rows.sort(key=lambda r: (not r["dir"], r["name"].lower()))
    out["total"] = len(rows)
    out["entries"] = rows[offset:offset + limit]
    return out

def disk_usage(relpath):
    """Recursive size/file/dir counts computed on demand. Dedupes hard-links by
    inode (so shared files aren't double counted). Does not follow symlinks."""
    abs_path, norm = resolve(relpath)
    size = files = dirs = errs = 0
    seen = set()
    for dp, dns, fns in os.walk(abs_path, followlinks=False, onerror=lambda e: None):
        dirs += len(dns)
        for f in fns:
            try:
                st = os.lstat(os.path.join(dp, f))
                if statmod.S_ISLNK(st.st_mode):
                    files += 1; continue
                key = (st.st_dev, st.st_ino)
                files += 1
                if key not in seen:
                    seen.add(key); size += st.st_size
            except OSError:
                errs += 1
    return {"path": norm, "size": size, "files": files, "dirs": dirs, "errors": errs}

def find(relpath, q, limit, max_scan):
    """Bounded name search under a subtree (safe on huge trees)."""
    abs_path, norm = resolve(relpath)
    ql = q.lower()
    hits, scanned = [], 0
    for dp, dns, fns in os.walk(abs_path, followlinks=False, onerror=lambda e: None):
        for name in dns + fns:
            scanned += 1
            if ql in name.lower():
                full = os.path.join(dp, name)
                rel = os.path.relpath(full, ROOT)
                isd = os.path.isdir(full)
                hits.append({"name": name, "rel": rel.replace(os.sep, "/"),
                             "dir": isd, "kind": 0 if isd else kind_of(name)})
                if len(hits) >= limit:
                    return {"hits": hits, "truncated": True, "scanned": scanned}
        if scanned >= max_scan:
            return {"hits": hits, "truncated": True, "scanned": scanned}
    return {"hits": hits, "truncated": False, "scanned": scanned}

# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        get = lambda k, d="": qs.get(k, [d])[0]
        try:
            if u.path == "/" or u.path == "/index.html":
                return self._html(PAGE)
            if u.path == "/api/config":
                return self._json({"root": ROOT, "show_hidden": SHOW_HIDDEN})
            if u.path == "/api/ls":
                off = max(0, int(get("offset", "0") or 0))
                lim = min(5000, max(1, int(get("limit", "1000") or 1000)))
                return self._json(list_dir(get("path", ""), off, lim))
            if u.path == "/api/du":
                return self._json(disk_usage(get("path", "")))
            if u.path == "/api/find":
                q = get("q", "").strip()
                if len(q) < 2:
                    return self._json({"hits": [], "truncated": False, "scanned": 0})
                lim = min(1000, max(1, int(get("limit", "400") or 400)))
                return self._json(find(get("path", ""), q, lim, max_scan=400000))
            self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            self._json({"error": repr(e)}, 500)

# ------------------------------------------------------------------ PAGE
PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>HPC File Browser</title>
<style>
 :root{--bg:#0f1420;--panel:#161d2e;--line:#2a3550;--txt:#dfe6f2;--dim:#8a97b0;--accent:#5eb0ff;
  --seq:#7ee0c0;--arch:#f6c177;--table:#9bd1ff;--doc:#c8b3ff;--nb:#ff9ecb;--code:#a6e3a1;--log:#8a97b0;--xml:#f0a6ff;--other:#8a97b0;}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:14px 18px;background:linear-gradient(180deg,#141b2c,#0f1420);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
 h1{margin:0;font-size:15px;color:#fff}
 .root{color:var(--dim);font-size:12px;word-break:break-all;margin-top:3px}
 .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
 input#q{flex:1;min-width:200px;background:var(--panel);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:8px 12px;font:inherit;font-size:13px}
 input#q:focus{outline:none;border-color:var(--accent)}
 button{background:var(--panel);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:7px 11px;font:inherit;font-size:12px;cursor:pointer}
 button:hover{border-color:var(--accent);color:#fff}
 label.hid{font-size:12px;color:var(--dim);display:flex;align-items:center;gap:5px;cursor:pointer}
 main{padding:10px 8px 50px}
 .row{display:flex;align-items:center;gap:6px;padding:2px 6px;border-radius:6px;white-space:nowrap}
 .row:hover{background:var(--panel)}
 .row.dir{cursor:pointer}
 .caret{width:14px;text-align:center;color:var(--dim);flex:0 0 auto;transition:transform .12s}
 .caret.open{transform:rotate(90deg)}
 .ic{width:16px;text-align:center;flex:0 0 auto}
 .nm{overflow:hidden;text-overflow:ellipsis}
 .dir .nm{color:#fff;font-weight:600}
 .link{color:var(--accent);font-size:10px;margin-left:4px}
 .meta{color:var(--dim);font-size:11px;margin-left:auto;padding-left:12px;flex:0 0 auto;display:flex;gap:10px;align-items:center}
 .du{border:1px solid var(--line);border-radius:6px;padding:1px 7px;font-size:11px;cursor:pointer;color:var(--dim)}
 .du:hover{border-color:var(--accent);color:#fff}
 .kids{margin-left:16px;border-left:1px solid var(--line);padding-left:2px;display:none}
 .kids.open{display:block}
 .more,.rf{color:var(--accent);font-size:12px;cursor:pointer;padding:2px 6px}
 .more:hover,.rf:hover{text-decoration:underline}
 .msg{color:var(--dim);font-size:12px;padding:2px 8px}
 .lock{color:#f6857f;font-size:11px;margin-left:6px}
 .hl{background:rgba(94,176,255,.25);border-radius:3px}
 #results{padding:4px 6px}
 .crumb{color:var(--dim);font-size:12px;margin-top:6px}
 .crumb b{color:var(--txt)}
 footer{position:fixed;bottom:0;left:0;right:0;background:#0c111c;border-top:1px solid var(--line);padding:5px 18px;font-size:11px;color:var(--dim)}
</style></head><body>
<header>
 <h1>HPC File Browser <span style="color:var(--dim);font-weight:400;font-size:12px">· live</span></h1>
 <div class="root" id="rootlabel">…</div>
 <div class="toolbar">
   <input id="q" placeholder="Search names under root…  (min 2 chars, bounded)">
   <button id="go">Search</button>
   <button id="reload">↻ Reload root</button>
   <label class="hid"><input type="checkbox" id="hidden"> show hidden</label>
 </div>
 <div class="crumb" id="crumb"></div>
</header>
<main>
 <div id="results" style="display:none"></div>
 <div id="tree"></div>
</main>
<footer><span id="foot">Ready.</span></footer>
<script>
const KINDS={0:{c:'var(--other)',i:'•'},1:{c:'var(--seq)',i:'🧬'},2:{c:'var(--arch)',i:'🗜'},
 3:{c:'var(--table)',i:'▦'},4:{c:'var(--doc)',i:'📄'},5:{c:'var(--nb)',i:'📓'},
 6:{c:'var(--code)',i:'⌨'},7:{c:'var(--log)',i:'📃'},8:{c:'var(--xml)',i:'⟨⟩'}};
let SHOW_HIDDEN=false;
function human(n){if(n==null)return'';n=+n;const u=['B','KB','MB','GB','TB','PB'];let i=0;
 while(n>=1024&&i<u.length-1){n/=1024;i++}return(i?n.toFixed(1):n)+u[i];}
function when(ts){if(!ts)return'';const d=new Date(ts*1000);return d.toISOString().slice(0,16).replace('T',' ');}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function foot(t){document.getElementById('foot').textContent=t;}
async function api(p){const r=await fetch(p);if(!r.ok)throw new Error(r.status);return r.json();}

async function lsInto(kidsEl, path, offset){
  const lim=1000;
  const data=await api(`/api/ls?path=${encodeURIComponent(path)}&offset=${offset}&limit=${lim}&_=${Date.now()}`);
  const old=kidsEl.querySelector(':scope > .more'); if(old) old.remove();
  if(!data.readable){
    const m=document.createElement('div');m.className='msg';
    m.innerHTML='<span class="lock">🔒 not readable</span> '+esc(data.error||'permission denied');
    kidsEl.appendChild(m);return;
  }
  if(data.total===0 && offset===0){
    const m=document.createElement('div');m.className='msg';m.textContent='(empty)';kidsEl.appendChild(m);return;
  }
  for(const ent of data.entries) kidsEl.appendChild(ent.dir?dirNode(ent,path):fileNode(ent));
  const shown=offset+data.entries.length;
  if(shown<data.total){
    const m=document.createElement('div');m.className='more';
    m.textContent=`▾ show more (${(data.total-shown).toLocaleString()} of ${data.total.toLocaleString()})`;
    m.onclick=(e)=>{e.stopPropagation();lsInto(kidsEl,path,shown);};
    kidsEl.appendChild(m);
  }
}

function fileNode(ent){
  const k=KINDS[ent.kind||0];
  const row=document.createElement('div');row.className='row';
  row.innerHTML=`<span class="caret"></span><span class="ic" style="color:${k.c}">${k.i}</span>`+
    `<span class="nm">${esc(ent.name)}</span>`+(ent.link?'<span class="link">↩</span>':'')+
    `<span class="meta"><span>${when(ent.mtime)}</span><span>${human(ent.size)}</span></span>`;
  return row;
}
function dirNode(ent, parentPath){
  const path=(parentPath?parentPath+'/':'')+ent.name;
  const wrap=document.createElement('div');
  const row=document.createElement('div');row.className='row dir';
  row.innerHTML=`<span class="caret">▶</span><span class="ic" style="color:var(--accent)">📁</span>`+
    `<span class="nm">${esc(ent.name)}</span>`+(ent.link?'<span class="link">↩</span>':'')+
    `<span class="meta"><span class="du" title="compute size">∑ size</span>`+
    `<span class="rf" title="refresh">↻</span></span>`;
  const kids=document.createElement('div');kids.className='kids';
  let loaded=false;
  async function open(){
    const c=row.querySelector('.caret');
    const isOpen=kids.classList.toggle('open');c.classList.toggle('open',isOpen);
    row.querySelector('.ic').textContent=isOpen?'📂':'📁';
    if(isOpen && !loaded){loaded=true;foot('Loading '+path+' …');try{await lsInto(kids,path,0);foot('Ready.');}catch(e){foot('Error: '+e);}}
  }
  row.addEventListener('click',open);
  row.querySelector('.du').addEventListener('click',async(e)=>{
    e.stopPropagation();const b=e.target;b.textContent='…';
    try{const d=await api(`/api/du?path=${encodeURIComponent(path)}`);
      b.textContent=`${human(d.size)} · ${d.files.toLocaleString()} files`+(d.errors?` · ${d.errors} err`:'');
      b.style.color='var(--seq)';b.style.cursor='default';
    }catch(err){b.textContent='∑ size';foot('du error: '+err);}
  });
  row.querySelector('.rf').addEventListener('click',async(e)=>{
    e.stopPropagation();kids.innerHTML='';loaded=true;
    if(!kids.classList.contains('open')){kids.classList.add('open');row.querySelector('.caret').classList.add('open');row.querySelector('.ic').textContent='📂';}
    foot('Refreshing '+path+' …');try{await lsInto(kids,path,0);foot('Ready.');}catch(err){foot('Error: '+err);}
  });
  wrap.appendChild(row);wrap.appendChild(kids);
  return wrap;
}

async function loadRoot(){
  const cfg=await api('/api/config');SHOW_HIDDEN=cfg.show_hidden;
  document.getElementById('rootlabel').textContent='root: '+cfg.root;
  document.getElementById('crumb').innerHTML='<b>'+esc(cfg.root)+'</b>';
  const tree=document.getElementById('tree');tree.innerHTML='';
  const kids=document.createElement('div');kids.className='kids open';tree.appendChild(kids);
  foot('Loading root …');await lsInto(kids,'',0);foot('Ready.');
}

async function doSearch(){
  const q=document.getElementById('q').value.trim();
  const box=document.getElementById('results');const tree=document.getElementById('tree');
  if(q.length<2){box.style.display='none';tree.style.display='';return;}
  box.style.display='';tree.style.display='none';box.innerHTML='<div class="msg">Searching…</div>';
  try{
    const d=await api(`/api/find?path=&q=${encodeURIComponent(q)}&limit=400`);
    if(!d.hits.length){box.innerHTML='<div class="msg">No matches ('+d.scanned.toLocaleString()+' scanned).</div>';return;}
    box.innerHTML='<div class="msg">'+d.hits.length+(d.truncated?'+ (truncated)':'')+' matches · '+d.scanned.toLocaleString()+' scanned</div>';
    for(const h of d.hits){
      const k=KINDS[h.kind||0];const r=document.createElement('div');r.className='row';
      const i=h.name.toLowerCase().indexOf(q.toLowerCase());
      const nm=i<0?esc(h.name):esc(h.name.slice(0,i))+'<span class="hl">'+esc(h.name.slice(i,i+q.length))+'</span>'+esc(h.name.slice(i+q.length));
      r.innerHTML=`<span class="ic" style="color:${h.dir?'var(--accent)':k.c}">${h.dir?'📁':k.i}</span>`+
        `<span class="nm">${nm}</span><span class="meta">${esc(h.rel)}</span>`;
      box.appendChild(r);
    }
  }catch(e){box.innerHTML='<div class="msg">Search error: '+e+'</div>';}
}

document.getElementById('go').onclick=doSearch;
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')doSearch();
  if(e.key==='Escape'){e.target.value='';doSearch();}});
document.getElementById('reload').onclick=loadRoot;
document.getElementById('hidden').onchange=async(e)=>{
  // toggle handled server-side only at launch; here we just re-request with a hint
  foot('Tip: restart the server with --show-hidden to include dotfiles.');
};
loadRoot();
</script></body></html>
"""

def main():
    global ROOT, SHOW_HIDDEN
    ap = argparse.ArgumentParser(description="Live HTML file-tree browser for large directories.")
    ap.add_argument("--root", default=os.getcwd(), help="directory to browse (default: cwd; use / for whole FS)")
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1; keep it localhost on shared clusters)")
    ap.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    ap.add_argument("--show-hidden", action="store_true", help="include dotfiles")
    args = ap.parse_args()
    ROOT = os.path.abspath(args.root)
    SHOW_HIDDEN = args.show_hidden
    if not os.path.isdir(ROOT):
        sys.exit(f"Not a directory: {ROOT}")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving {ROOT}\n  browse: http://{args.host}:{args.port}\n"
          f"  tunnel: ssh -L {args.port}:localhost:{args.port} <user>@<login-node>\n"
          f"Ctrl-C to stop.", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)

if __name__ == "__main__":
    main()
