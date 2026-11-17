"""Fixture download/extraction tests. Never downloads or runs the Windows binary."""
from pathlib import Path
import io,json,sys,tempfile,hashlib,threading,zipfile,struct,unittest,urllib.request
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT))
import portable_ollama as m
class Response(io.BytesIO):
 def __init__(self,data,status=200,headers=None):super().__init__(data);self.status=status;self.headers=headers or {'Content-Length':str(len(data))}
class Portable(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.stop=threading.Event();self.events=[]
 def tearDown(self):self.tmp.cleanup()
 def test_download_resume_and_no_repeat(self):
  body=b'fixture-download-contents';dest=self.base/'runtime.zip';part=dest.with_suffix('.zip.part');part.write_bytes(body[:7]);seen=[]
  def opener(request,timeout):seen.append(request);return Response(body[7:],206,{'Content-Length':str(len(body)-7),'Content-Range':f'bytes 7-{len(body)-1}/{len(body)}'})
  with patch.object(m,'ARCHIVE_SHA256',hashlib.sha256(body).hexdigest()):
   self.assertEqual(m.download_archive(dest,self.events.append,self.stop,opener).read_bytes(),body);self.assertEqual(seen[0].get_header('Range'),'bytes=7-')
   m.download_archive(dest,self.events.append,self.stop,lambda *a,**k:(_ for _ in ()).throw(AssertionError('Unnecessary network request')))
 def test_complete_part_reused(self):
  body=b'finished fixture';dest=self.base/'runtime.zip';dest.with_suffix('.zip.part').write_bytes(body)
  with patch.object(m,'ARCHIVE_SHA256',hashlib.sha256(body).hexdigest()):m.download_archive(dest,self.events.append,self.stop,lambda *a,**k:(_ for _ in ()).throw(AssertionError('Complete part must not re-download')))
  self.assertEqual(dest.read_bytes(),body)
 def test_server_ignores_range(self):
  body=b'complete';dest=self.base/'runtime.zip';dest.with_suffix('.zip.part').write_bytes(b'old-prefix')
  with patch.object(m,'ARCHIVE_SHA256',hashlib.sha256(body).hexdigest()):m.download_archive(dest,self.events.append,self.stop,lambda *a,**k:Response(body))
  self.assertEqual(dest.read_bytes(),body)
 def test_mismatch_and_cancel(self):
  dest=self.base/'runtime.zip'
  with self.assertRaises(RuntimeError):m.download_archive(dest,self.events.append,self.stop,lambda *a,**k:Response(b'corrupt'))
  self.assertFalse(dest.exists());self.assertTrue(list(self.base.glob('*.invalid-*')))
  self.stop.set()
  with self.assertRaises(RuntimeError):m.download_archive(dest,self.events.append,self.stop,lambda *a,**k:Response(b'anything'))
 def zip(self,bad=None):
  p=self.base/'fixture.zip';exe=bytearray(80);exe[:2]=b'MZ';struct.pack_into('<I',exe,0x3c,64);exe[64:68]=b'PE\0\0';struct.pack_into('<H',exe,68,0x8664)
  with zipfile.ZipFile(p,'w') as z:
   z.writestr('ollama.exe',bytes(exe));z.writestr('lib/ollama/example.txt','test fixture, not a DLL')
   if bad:z.writestr(bad,b'bad')
  return p
 def test_extract_and_marker(self):
  p=self.zip();base=self.base/'install';exe=m.extract_archive(p,base,self.events.append,self.stop);self.assertTrue(Path(exe).is_file());self.assertEqual(m.installed_executable(base),exe)
 def test_reject_archive_paths(self):
  for bad in ['../escape','C:/escape','/absolute','lib/../../escape']:
   with self.subTest(bad=bad):
    p=self.zip(bad)
    with self.assertRaises(RuntimeError):m.extract_archive(p,self.base/bad.replace('/','_').replace(':','_'),self.events.append,self.stop)
  self.assertFalse((self.base/'escape').exists())
 def test_model_directory_only_for_owned_runtime(self):
  import local_ai
  client=local_ai.Ollama(self.base/'logs');states=iter([{'connected':False},{'connected':True}]);client.status=lambda:next(states);client.executable=lambda:'C:/owned/ollama.exe'
  class Stop:
   def wait(self,n):return False
  with patch.object(local_ai.portable_ollama,'installed_executable',return_value='C:/owned/ollama.exe'),patch.object(local_ai.subprocess,'Popen') as start:
   client.ensure(Stop());env=start.call_args.kwargs['env'];self.assertEqual(env['OLLAMA_HOST'],'127.0.0.1:11434');self.assertEqual(env['OLLAMA_NO_CLOUD'],'1');self.assertTrue(env['OLLAMA_MODELS'].endswith('ollama_models'))
if __name__=='__main__':unittest.main(verbosity=2)
