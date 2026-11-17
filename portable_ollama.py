"""Pinned official Windows Ollama runtime, prepared inside this project only."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,platform,shutil,struct,tempfile,threading,time,urllib.request,urllib.error,zipfile
ROOT=Path(__file__).resolve().parent
VERSION='0.40.7'
ARCHIVE_SHA256='e6527768c53e85ec4596495fb0f035ef07f437b4b591ebc3581684699a4668f4'
DOWNLOAD_URL='https://github.com/ollama/ollama/releases/download/v0.40.7/ollama-windows-amd64.zip'
BASE=ROOT/'.runtime/ollama'
LOCK=threading.Lock()
def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def installed_executable(base=BASE):
 folder=Path(base)/VERSION;exe=folder/'ollama.exe'
 try:
  marker=json.loads((folder/'ready.json').read_text(encoding='utf-8'))
  if marker.get('archive_sha256')==ARCHIVE_SHA256 and exe.is_file():return str(exe.resolve())
 except (OSError,ValueError):pass
 return None

def download_archive(destination,progress,stop,opener=urllib.request.urlopen):
 destination=Path(destination)
 if destination.is_file() and digest(destination)==ARCHIVE_SHA256:return destination
 part=destination.with_suffix('.zip.part')
 if part.is_file() and digest(part)==ARCHIVE_SHA256:part.replace(destination);return destination
 offset=part.stat().st_size if part.exists() else 0
 request=urllib.request.Request(DOWNLOAD_URL,headers={'User-Agent':'WG-Solar-Lab/24','Range':f'bytes={offset}-'} if offset else {'User-Agent':'WG-Solar-Lab/24'})
 try:response=opener(request,timeout=60)
 except urllib.error.HTTPError as error:
  if error.code==416 and part.exists():
   part.replace(part.with_name(part.name+'.invalid-'+str(time.time_ns())))
   raise RuntimeError('续传缓存已失效；重新点击准备可重新下载') from error
  raise
 with response:
  status=getattr(response,'status',200)
  if status==206:
   content_range=response.headers.get('Content-Range','')
   if not content_range.startswith(f'bytes {offset}-'):raise RuntimeError('运行程序下载续传位置不一致，请重试')
  elif status==200:offset=0
  else:raise RuntimeError(f'运行程序下载响应无效：{status}')
  size=response.headers.get('Content-Length');total=offset+int(size) if size else None
  if total and total>8*1024**3:raise RuntimeError('运行程序下载大小超出预期')
  done=offset;last=0
  with part.open('ab' if offset else 'wb') as f:
   while True:
    if stop.is_set():raise RuntimeError('运行程序准备已中断，可稍后重试')
    chunk=response.read(1024*1024)
    if not chunk:break
    f.write(chunk);done+=len(chunk)
    if done>8*1024**3:raise RuntimeError('运行程序下载过大')
    if time.monotonic()-last>.7:
     progress({'status':'下载本地运行程序','completed':done,'total':total or 0});last=time.monotonic()
  if total is not None and done!=total:raise RuntimeError('运行程序下载不完整，可重试续传')
 progress({'status':'校验本地运行程序','completed':done,'total':done})
 if digest(part)!=ARCHIVE_SHA256:
  # Keep the rejected file for inspection; never execute it or keep resuming bad bytes.
  rejected=part.with_name(part.name+'.invalid-'+str(time.time_ns()));part.replace(rejected)
  raise RuntimeError('运行程序SHA256校验失败；未执行下载内容，请重试')
 part.replace(destination);return destination

def extract_archive(archive,base,progress,stop):
 base=Path(base);base.mkdir(parents=True,exist_ok=True);target=base/VERSION
 if target.exists():raise RuntimeError('本地AI目录不完整；请将 .runtime/ollama/'+VERSION+' 改名备份后重试')
 with tempfile.TemporaryDirectory(prefix=VERSION+'-',dir=base) as temporary:
  stage=Path(temporary)/'runtime';stage.mkdir()
  with zipfile.ZipFile(archive) as z:
   if sum(i.file_size for i in z.infolist())>12*1024**3:raise RuntimeError('运行程序解压大小超出预期')
   for member in z.infolist():
    name=member.filename.replace('\\','/');path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name or (member.external_attr>>16)&0o170000==0o120000:raise RuntimeError('运行程序压缩包包含不安全路径')
    if stop.is_set():raise RuntimeError('运行程序解压已中断')
    destination=stage.joinpath(*path.parts)
    if member.is_dir():destination.mkdir(parents=True,exist_ok=True);continue
    destination.parent.mkdir(parents=True,exist_ok=True)
    with z.open(member) as src,destination.open('wb') as dst:shutil.copyfileobj(src,dst,1024*1024)
  exe=stage/'ollama.exe'
  with exe.open('rb') as f:
   head=f.read(64)
   if head[:2]!=b'MZ':raise RuntimeError('运行程序不是有效Windows文件')
   f.seek(struct.unpack_from('<I',head,0x3c)[0]);pe=f.read(6)
   if pe[:4]!=b'PE\0\0' or struct.unpack_from('<H',pe,4)[0]!=0x8664:raise RuntimeError('运行程序不是Windows x64版本')
  (stage/'ready.json').write_text(json.dumps({'version':VERSION,'archive_sha256':ARCHIVE_SHA256}),encoding='utf-8')
  stage.rename(target)
 progress({'status':'本地运行程序已准备好'})
 return str((target/'ollama.exe').resolve())

def prepare_runtime(progress,stop):
 if os.name!='nt' or platform.machine().lower() not in ('amd64','x86_64'):raise RuntimeError('自动准备运行程序适用于Windows x64；其它系统请先安装Ollama')
 with LOCK:
  exe=installed_executable()
  if exe:return exe
  BASE.mkdir(parents=True,exist_ok=True)
  if shutil.disk_usage(BASE).free<8*1024**3:raise RuntimeError('本地AI首次准备需要至少8GB可用空间，建议预留10GB')
  archive=download_archive(BASE/('ollama-windows-amd64-'+VERSION+'.zip'),progress,stop)
  progress({'status':'解压本地运行程序'})
  return extract_archive(archive,BASE,progress,stop)
