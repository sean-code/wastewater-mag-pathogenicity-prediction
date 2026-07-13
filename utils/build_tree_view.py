#!/usr/bin/env python3
"""
build_tree_view.py  --  Interactive HTML tree viewer for the datasets/ directory.

Walks a directory (default: current dir), computes per-folder file counts and
aggregate sizes, classifies files by type, and writes a SELF-CONTAINED, offline
HTML file you can open in any browser. Re-run it any time to refresh the tree
after new files are downloaded.

Usage:
    python build_tree_view.py                        # scan ., write datasets_tree.html
    python build_tree_view.py /path/to/datasets      # scan a specific root
    python build_tree_view.py . -o tree.html         # custom output name
    python build_tree_view.py . --max-depth 3        # limit depth
    python build_tree_view.py . --exclude '.git' '__pycache__'

View it:
    - scp the .html to your laptop and double-click, OR
    - open it from the Jupyter file browser, OR
    - `python -m http.server` in the folder and browse to it.
"""
import os, sys, json, argparse, fnmatch, datetime, html

# ---- file-type classification (kind codes used by the viewer JS) ----
KIND = {
    "seq":  (1, [".fa", ".fasta", ".fna", ".faa", ".ffn", ".fa.gz", ".fasta.gz",
                 ".fna.gz", ".fastq", ".fastq.gz", ".fq", ".fq.gz", ".gz", ".gbff"]),
    "arch": (2, [".zip", ".tar", ".tgz", ".tar.gz", ".bz2", ".xz"]),
    "table":(3, [".csv", ".tsv", ".json", ".jsonl", ".parquet"]),
    "doc":  (4, [".docx", ".doc", ".pdf", ".txt", ".md", ".rtf"]),
    "nb":   (5, [".ipynb"]),
    "code": (6, [".py", ".sh", ".r", ".pl", ".js"]),
    "log":  (7, [".log"]),
    "xml":  (8, [".xml"]),
}

def kind_of(name):
    low = name.lower()
    for _, (code, exts) in KIND.items():
        for e in exts:
            if low.endswith(e):
                return code
    return 0

DEFAULT_EXCLUDE = [".git", "__pycache__", ".DS_Store"]

def scan(root, excludes, max_depth, follow_symlinks, seen, depth=0):
    """Return (node_dict, size_listed, files, dirs, size_unique).
    size_listed sums every file; size_unique counts each inode once (so hard-links,
    e.g. folder 07 -> folder 02, are not double-counted) -> matches disk usage."""
    name = os.path.basename(os.path.abspath(root)) or root
    node = {"n": name, "sz": 0, "fc": 0, "ch": []}
    tot_size = tot_files = tot_dirs = tot_uniq = 0
    try:
        entries = sorted(os.scandir(root), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    except (PermissionError, FileNotFoundError, OSError):
        return node, 0, 0, 0, 0
    for e in entries:
        if any(fnmatch.fnmatch(e.name, pat) for pat in excludes):
            continue
        is_dir = e.is_dir(follow_symlinks=follow_symlinks)
        if is_dir:
            if max_depth is not None and depth + 1 > max_depth:
                # summarize collapsed subtree size without expanding children
                sub_sz, sub_fc, sub_uq = _quick_size(e.path, excludes, follow_symlinks, seen)
                node["ch"].append({"n": e.name + "/", "sz": sub_sz, "fc": sub_fc, "ch": [], "collapsed": 1})
                node["sz"] += sub_sz; node["fc"] += sub_fc
                tot_size += sub_sz; tot_files += sub_fc; tot_dirs += 1; tot_uniq += sub_uq
                continue
            child, csz, cfc, cdirs, cuq = scan(e.path, excludes, max_depth, follow_symlinks, seen, depth + 1)
            node["ch"].append(child)
            node["sz"] += csz; node["fc"] += cfc
            tot_size += csz; tot_files += cfc; tot_dirs += 1 + cdirs; tot_uniq += cuq
        else:
            try:
                st = e.stat(follow_symlinks=follow_symlinks)
                sz = st.st_size
                key = (st.st_dev, st.st_ino)
            except (OSError, ValueError):
                sz = 0; key = None
            uq = 0
            if key is not None and key not in seen:
                seen.add(key); uq = sz
            link = e.is_symlink()
            node["ch"].append({"n": e.name, "sz": sz, "k": kind_of(e.name), **({"L": 1} if link else {})})
            node["sz"] += sz; node["fc"] += 1
            tot_size += sz; tot_files += 1; tot_uniq += uq
    return node, tot_size, tot_files, tot_dirs, tot_uniq

def _quick_size(path, excludes, follow_symlinks, seen):
    sz = fc = uq = 0
    for dp, dns, fns in os.walk(path, followlinks=follow_symlinks):
        dns[:] = [d for d in dns if not any(fnmatch.fnmatch(d, p) for p in excludes)]
        for f in fns:
            if any(fnmatch.fnmatch(f, p) for p in excludes):
                continue
            try:
                st = os.stat(os.path.join(dp, f))
                sz += st.st_size; fc += 1
                key = (st.st_dev, st.st_ino)
                if key not in seen:
                    seen.add(key); uq += st.st_size
            except OSError:
                pass
    return sz, fc, uq

# ------------------------------------------------------------------ HTML
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#0f1420; --panel:#161d2e; --panel2:#1d2740; --line:#2a3550;
    --txt:#dfe6f2; --dim:#8a97b0; --accent:#5eb0ff; --accent2:#7ee0c0;
    --seq:#7ee0c0; --arch:#f6c177; --table:#9bd1ff; --doc:#c8b3ff; --nb:#ff9ecb;
    --code:#a6e3a1; --log:#8a97b0; --xml:#f0a6ff; --other:#8a97b0;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  header{padding:16px 20px;background:linear-gradient(180deg,#141b2c,#0f1420);
         border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
  h1{margin:0 0 4px;font-size:16px;font-weight:600;color:#fff}
  .path{color:var(--dim);font-size:12px;word-break:break-all}
  .stats{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--dim)}
  .stats b{color:var(--txt)}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px}
  input#q{flex:1;min-width:180px;background:var(--panel);border:1px solid var(--line);
          color:var(--txt);border-radius:8px;padding:8px 12px;font:inherit;font-size:13px}
  input#q:focus{outline:none;border-color:var(--accent)}
  button{background:var(--panel);border:1px solid var(--line);color:var(--txt);
         border-radius:8px;padding:7px 12px;font:inherit;font-size:12px;cursor:pointer}
  button:hover{border-color:var(--accent);color:#fff}
  .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .chip{font-size:11px;padding:3px 9px;border-radius:20px;border:1px solid var(--line);
        cursor:pointer;user-select:none;color:var(--dim);background:var(--panel)}
  .chip.on{color:#0f1420;font-weight:600}
  main{padding:12px 8px 60px}
  .node{margin:0}
  .row{display:flex;align-items:center;gap:6px;padding:2px 6px;border-radius:6px;cursor:default;white-space:nowrap}
  .row:hover{background:var(--panel)}
  .row.dir{cursor:pointer}
  .caret{width:14px;text-align:center;color:var(--dim);transition:transform .12s;flex:0 0 auto}
  .caret.open{transform:rotate(90deg)}
  .ic{width:16px;text-align:center;flex:0 0 auto}
  .nm{overflow:hidden;text-overflow:ellipsis}
  .dir .nm{color:#fff;font-weight:600}
  .badge{color:var(--dim);font-size:11px;margin-left:6px;flex:0 0 auto}
  .sz{color:var(--dim);font-size:11px;margin-left:auto;padding-left:12px;flex:0 0 auto}
  .link{color:var(--accent);font-size:10px;margin-left:4px}
  .kids{margin-left:16px;border-left:1px solid var(--line);padding-left:2px;display:none}
  .kids.open{display:block}
  .more{color:var(--accent);font-size:12px;cursor:pointer;padding:3px 6px}
  .more:hover{text-decoration:underline}
  .hl{background:rgba(94,176,255,.25);border-radius:3px}
  .empty{color:var(--dim);padding:20px;text-align:center}
  footer{position:fixed;bottom:0;left:0;right:0;background:#0c111c;border-top:1px solid var(--line);
         padding:5px 20px;font-size:11px;color:var(--dim);display:flex;gap:16px}
</style></head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="path">__ROOT__</div>
  <div class="stats">
    <span><b id="s-dirs">0</b> folders</span>
    <span><b id="s-files">0</b> files</span>
    <span><b id="s-size">0</b> on disk</span>
    <span id="s-listed" style="color:var(--dim)"></span>
    <span>generated <b>__WHEN__</b></span>
  </div>
  <div class="toolbar">
    <input id="q" placeholder="Search files &amp; folders…  (min 2 chars)">
    <button id="expand">Expand all</button>
    <button id="collapse">Collapse all</button>
  </div>
  <div class="chips" id="chips"></div>
</header>
<main><div id="tree"></div></main>
<footer><span>Click a folder to expand.</span><span id="foot-count"></span></footer>

<script>
const DATA = __TREE_DATA__;
const META = __META__;
const CHUNK = 300;
const KINDS = {
  0:{c:'var(--other)',i:'•',l:'other'}, 1:{c:'var(--seq)',i:'🧬',l:'sequence'},
  2:{c:'var(--arch)',i:'🗜',l:'archive'}, 3:{c:'var(--table)',i:'▦',l:'table/meta'},
  4:{c:'var(--doc)',i:'📄',l:'doc'}, 5:{c:'var(--nb)',i:'📓',l:'notebook'},
  6:{c:'var(--code)',i:'⌨',l:'code'}, 7:{c:'var(--log)',i:'📃',l:'log'},
  8:{c:'var(--xml)',i:'⟨⟩',l:'xml'}
};
const activeKinds = new Set(Object.keys(KINDS).map(Number));
function human(n){n=+n;const u=['B','KB','MB','GB','TB','PB'];let i=0;
  while(n>=1024&&i<u.length-1){n/=1024;i++}return (i?n.toFixed(1):n)+u[i];}

// stats (from Python; size 'on disk' dedupes hard-links by inode)
document.getElementById('s-dirs').textContent=(META.dirs||0).toLocaleString();
document.getElementById('s-files').textContent=(META.files||0).toLocaleString();
document.getElementById('s-size').textContent=human(META.unique||0);
if((META.listed||0) > (META.unique||0))
  document.getElementById('s-listed').textContent='('+human(META.listed)+' listed incl. hard-links)';

// chips
const chipBox=document.getElementById('chips');
for(const [code,meta] of Object.entries(KINDS)){
  const el=document.createElement('span');
  el.className='chip on';el.style.borderColor=meta.c;el.style.background=meta.c;
  el.textContent=meta.i+' '+meta.l;el.dataset.k=code;
  el.onclick=()=>{const k=+code; if(activeKinds.has(k)){activeKinds.delete(k);el.classList.remove('on');el.style.background='var(--panel)';el.style.color='var(--dim)';}
    else{activeKinds.add(k);el.classList.add('on');el.style.background=meta.c;el.style.color='#0f1420';}
    render();};
  chipBox.appendChild(el);
}

const treeEl=document.getElementById('tree');
let query='';

function matches(node){
  const q=query;
  if(!q) return true;
  if(node.n.toLowerCase().includes(q)) return true;
  if(node.ch){for(const c of node.ch) if(matches(c)) return true;}
  return false;
}
function fileVisible(node){ return activeKinds.has(node.k||0) && (!query || node.n.toLowerCase().includes(query)); }

function makeFile(node){
  if(!fileVisible(node)) return null;
  const meta=KINDS[node.k||0];
  const row=document.createElement('div');row.className='row';
  row.innerHTML=`<span class="caret"></span><span class="ic" style="color:${meta.c}">${meta.i}</span>`+
    `<span class="nm">${hlt(node.n)}</span>`+(node.L?'<span class="link">↩link</span>':'')+
    `<span class="sz">${human(node.sz||0)}</span>`;
  return row;
}
function hlt(name){
  if(!query) return esc(name);
  const i=name.toLowerCase().indexOf(query);
  if(i<0) return esc(name);
  return esc(name.slice(0,i))+'<span class="hl">'+esc(name.slice(i,i+query.length))+'</span>'+esc(name.slice(i+query.length));
}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

function makeDir(node,depth){
  if(query && !matches(node)) return null;
  const wrap=document.createElement('div');wrap.className='node';
  const row=document.createElement('div');row.className='row dir';
  const openInit = !!query || depth<1;
  row.innerHTML=`<span class="caret ${openInit?'open':''}">▶</span>`+
    `<span class="ic" style="color:var(--accent)">${node.collapsed?'📁…':'📂'}</span>`+
    `<span class="nm">${hlt(node.n)}</span>`+
    `<span class="badge">${(node.fc||0).toLocaleString()} files</span>`+
    `<span class="sz">${human(node.sz||0)}</span>`;
  const kids=document.createElement('div');kids.className='kids'+(openInit?' open':'');
  let built=false, shown=0;
  function buildChunk(){
    const children=(node.ch||[]);
    const dirs=children.filter(c=>c.ch), files=children.filter(c=>!c.ch);
    const ordered=dirs.concat(files);
    let added=0;
    while(shown<ordered.length && added<CHUNK){
      const c=ordered[shown++];
      const el=c.ch?makeDir(c,depth+1):makeFile(c);
      if(el) kids.appendChild(el);
      added++;
    }
    const old=kids.querySelector(':scope > .more'); if(old) old.remove();
    if(shown<ordered.length){
      const m=document.createElement('div');m.className='more';
      m.textContent=`▾ show more (${(ordered.length-shown).toLocaleString()} remaining)`;
      m.onclick=(e)=>{e.stopPropagation();buildChunk();};
      kids.appendChild(m);
    }
  }
  function ensure(){if(!built){buildChunk();built=true;}}
  if(openInit) ensure();
  row.onclick=()=>{const c=row.querySelector('.caret');
    const open=kids.classList.toggle('open');c.classList.toggle('open',open);
    row.querySelector('.ic').textContent=node.collapsed?'📁…':(open?'📂':'📁');
    if(open) ensure();};
  wrap.appendChild(row);wrap.appendChild(kids);
  return wrap;
}

function render(){
  treeEl.innerHTML='';
  const root=makeDir(DATA,0);
  if(root){ // force root open
    treeEl.appendChild(root);
    const c=root.querySelector('.caret'), k=root.querySelector('.kids');
    c.classList.add('open');k.classList.add('open');
  } else {
    treeEl.innerHTML='<div class="empty">No matches.</div>';
  }
}

let t=null;
document.getElementById('q').addEventListener('input',e=>{
  clearTimeout(t);
  t=setTimeout(()=>{const v=e.target.value.trim().toLowerCase();
    query=v.length>=2?v:''; render();},180);
});
document.getElementById('expand').onclick=()=>{
  let changed=true, guard=0;
  while(changed && guard<60){
    changed=false; guard++;
    document.querySelectorAll('.row.dir').forEach(r=>{
      const k=r.parentElement.querySelector(':scope > .kids');
      if(k && !k.classList.contains('open')){ r.click(); changed=true; }
    });
  }
};
document.getElementById('collapse').onclick=()=>{render();};

render();
</script>
</body></html>
"""

def build_html(node, root_abs, title, meta):
    data = json.dumps(node, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    metaj = json.dumps(meta, separators=(",", ":"))
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = (TEMPLATE
           .replace("__TREE_DATA__", data)
           .replace("__META__", metaj)
           .replace("__TITLE__", html.escape(title))
           .replace("__ROOT__", html.escape(root_abs))
           .replace("__WHEN__", when))
    return out

def main():
    ap = argparse.ArgumentParser(description="Build an interactive HTML tree of a directory.")
    ap.add_argument("root", nargs="?", default=".", help="directory to scan (default: .)")
    ap.add_argument("-o", "--output", default="datasets_tree.html", help="output HTML file")
    ap.add_argument("--max-depth", type=int, default=None, help="limit tree depth")
    ap.add_argument("--exclude", nargs="*", default=[], help="extra glob patterns to skip")
    ap.add_argument("--follow-symlinks", action="store_true", help="follow symlinked dirs")
    ap.add_argument("--title", default=None, help="title shown in the header")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    excludes = DEFAULT_EXCLUDE + list(args.exclude)
    # never recurse into our own output or checkpoints
    excludes += [os.path.basename(args.output), ".ipynb_checkpoints"]
    title = args.title or f"Datasets tree — {os.path.basename(root) or root}"

    print(f"Scanning {root} ...", file=sys.stderr)
    seen = set()
    node, tsize, tfiles, tdirs, tuniq = scan(root, excludes, args.max_depth, args.follow_symlinks, seen)
    meta = {"dirs": tdirs, "files": tfiles, "listed": tsize, "unique": tuniq}
    out = build_html(node, root, title, meta)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Wrote {args.output}: {tdirs:,} folders, {tfiles:,} files, "
          f"{tuniq/1e9:.2f} GB on disk ({tsize/1e9:.2f} GB listed incl. hard-links) "
          f"-> {len(out)/1e6:.1f} MB HTML", file=sys.stderr)

if __name__ == "__main__":
    main()
