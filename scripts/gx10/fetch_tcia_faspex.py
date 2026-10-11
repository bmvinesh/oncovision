"""Download a TCIA Faspex v5 public package with ascp (aarch64 Transfer SDK).
usage: fetch_faspex.py <context_b64> <dest_dir> [relative_file_list.txt]   # context = ?context= value from the TCIA page link
"""
import json,base64,urllib.parse as u,urllib.request as r,urllib.error,os,subprocess,sys
H="https://faspex.cancerimagingarchive.net"; CID="ff9aa63a-72e1-436f-82ef-5677eb1f7aee"
ctx_b64,dest=sys.argv[1],sys.argv[2]
ctx=json.loads(base64.b64decode(ctx_b64)); pkg=ctx["package_id"]
state=base64.b64encode(json.dumps(ctx,separators=(',',':')).encode()).decode()
class NoRedir(r.HTTPRedirectHandler):
    def redirect_request(self,*a,**k): return None
try: r.build_opener(NoRedir).open(f"{H}/aspera/faspex/auth/authorize_public_link?&response_type=code&client_id={CID}&redirect_uri=/aspera/faspex/token&state={u.quote(state,safe='')}")
except urllib.error.HTTPError as e: loc=e.headers["location"]
code=u.parse_qs(u.urlparse(loc).query)["code"][0]
def post(path,body,hdr=None):
    q=r.Request(H+path,data=json.dumps(body).encode(),headers={"Content-Type":"application/json",**(hdr or {})})
    with r.urlopen(q) as f: return json.loads(f.read())
tok=post("/aspera/faspex/auth/token",{"code":code,"state":state,"grant_type":"authorization_code","client_id":CID,"redirect_uri":"/aspera/faspex/token"})["access_token"]
ts=post(f"/aspera/faspex/api/v5/packages/{pkg}/transfer_spec/download?transfer_type=connect&type=received",{},{"Authorization":"Bearer "+tok})
src=ts["paths"][0]["source"]; os.makedirs(dest,exist_ok=True)
env=dict(os.environ,ASPERA_SCP_TOKEN=ts["token"],ASPERA_SCP_COOKIE=ts["cookie"])
sdk=os.path.expanduser("~/tools/aspera-sdk/ibm-aspera-transfer-sdk-linux-aarch64-1.1.7")
cmd=[sdk+"/bin/ascp","-P",str(ts["ssh_port"]),"-O",str(ts["fasp_port"]),"-l","400m","-k","2","-d","--mode","recv",
     "--user",ts["remote_user"],"--host",ts["remote_host"],"-i",os.path.expanduser("~/tools/aspera-sdk/bypass_rsa.pem"),
     "--policy","fair","-c","aes128","--overwrite=diff"]
if len(sys.argv) > 3:
    # transfer only the listed files (paths relative to the package folder), keeping directory structure
    base = src.rstrip("/")
    lst = os.path.abspath(sys.argv[3]) + ".abs"
    with open(sys.argv[3]) as f, open(lst, "w") as g:
        for line in f:
            if line.strip():
                g.write(base + "/" + line.strip().lstrip("/") + "\n")
    cmd += ["--src-base=" + base, "--file-list=" + lst, dest]
else:
    cmd += [src, dest]
print("package",pkg,"src",src,flush=True)
sys.exit(subprocess.call(cmd,env=env))
