"""Optional token-protected, read-only LAN viewing. Default startup stays loopback-only."""
import ipaddress,hmac
from urllib.parse import parse_qs,urlencode
from http.cookies import SimpleCookie
from starlette.requests import Request
from starlette.responses import JSONResponse,RedirectResponse

def is_local(host):
 if host=='testclient':return True
 try:return ipaddress.ip_address(host).is_loopback
 except ValueError:return False
class LocalAccess:
 def __init__(self,app,settings):self.app=app;self.settings=settings
 async def __call__(self,scope,receive,send):
  if scope['type'] not in ('http','websocket'):return await self.app(scope,receive,send)
  peer=(scope.get('client') or ('',0))[0];local=is_local(peer)
  if local:
   if scope['type']=='http' and scope['method'] not in ('GET','HEAD','OPTIONS'):
    r=Request(scope);origin=r.headers.get('origin')
    if origin and origin!=str(r.base_url).rstrip('/'):
     return await JSONResponse({'detail':'拒绝跨来源操作'},403)(scope,receive,send)
   return await self.app(scope,receive,send)
  settings=self.settings();token=settings.get('token','');mode=settings.get('lan',False)
  try:private=ipaddress.ip_address(peer).is_private
  except ValueError:private=False
  headers=dict(scope.get('headers',[]));cookies=SimpleCookie()
  try:cookies.load(headers.get(b'cookie',b'').decode('latin-1'))
  except Exception:pass
  got=cookies['wg_lan_access'].value if 'wg_lan_access' in cookies else ''
  authed=bool(token and got and hmac.compare_digest(got.encode(),token.encode()))
  query=parse_qs(scope.get('query_string',b'').decode('utf-8',errors='replace'))
  if scope['type']=='http' and mode and private and token and scope['method']=='GET' and len(query.get('access',[]))==1 and hmac.compare_digest(query['access'][0].encode(),token.encode()):
   query.pop('access');url=scope.get('path','/')+('?' + urlencode(query,doseq=True) if query else '')
   response=RedirectResponse(url,status_code=303);response.set_cookie('wg_lan_access',token,httponly=True,samesite='strict',max_age=86400);response.headers['Cache-Control']='no-store';return await response(scope,receive,send)
  if not(mode and private and authed):
   if scope['type']=='websocket':return await send({'type':'websocket.close','code':1008})
   return await JSONResponse({'detail':'请使用主机提供的完整局域网访问地址'},403)(scope,receive,send)
  if scope['type']=='http' and scope['method'] not in ('GET','HEAD','OPTIONS'):return await JSONResponse({'detail':'局域网为只读访问；请在主机操作仿真或AI任务'},403)(scope,receive,send)
  await self.app(scope,receive,send)
