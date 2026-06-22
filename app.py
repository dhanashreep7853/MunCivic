"""
app.py — MunCivic
"""
#flask is web framework,in flask import some toolkit
from flask import Flask, jsonify, render_template, request, redirect, session, url_for, flash #toolkits
from datetime import datetime, timedelta #datetime-specific point in time, timedelta-represent duration, use to find deadline
from functools import wraps #preservesoriginal function's identity
import random, string, os, smtplib, json, tempfile, uuid, threading, re # python built-in standard liabrary
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
load_dotenv() #read .env file 
from werkzeug.security import generate_password_hash, check_password_hash #werkzeug flask foundation library
from werkzeug.utils import secure_filename

#flask extensions - separate packages installed via pip 
from flask_sqlalchemy import SQLAlchemy # talk with db usinng python
from flask_wtf.csrf import CSRFProtect # add CSRF token for post request
from flask_limiter import Limiter #rate limiting, count request per ip address
from flask_limiter.util import get_remote_address # function that extracts the user's IP from the request

# IMAGE TYPE DETECTION — works on Python 3.9 to 3.13+ (no imghdr)
try:
    import filetype as _filetype
    def _detect_image_type(b):
        k = _filetype.guess(b)
        return k.mime.split("/")[-1] if k and k.mime.startswith("image/") else ""
except ImportError:
    def _detect_image_type(b):
        if len(b) < 12: return ""
        if b[:8] == b'\x89PNG\r\n\x1a\n': return "png"
        if b[:3] == b'\xff\xd8\xff': return "jpeg"
        if b[:6] in (b'GIF87a', b'GIF89a'): return "gif"
        if b[:4] == b'RIFF' and b[8:12] == b'WEBP': return "webp"
        return ""

# GEMINI AI
AI_AVAILABLE, genai_client = False, None
try:
    from google import genai
    from google.genai import types as genai_types
    _key = os.getenv("GEMINI_API_KEY","").strip()
    if _key:
        genai_client = genai.Client(api_key=_key)
        AI_AVAILABLE = True
        print(f"[Gemini] Ready. Key:{_key[:8]}...")
except ImportError:
    pass

app = Flask(__name__) #create flask application object, contain current file name

SECRET_KEY = os.getenv("SECRET_KEY","").strip()  
IS_DEV = os.getenv("FLASK_ENV") == "development"
if not SECRET_KEY:
    if not IS_DEV:
        raise RuntimeError("FATAL: SECRET_KEY not set in environment variables.")
    SECRET_KEY = "dev_only_key_NOT_for_production"
app.secret_key = SECRET_KEY

#Four cookie security settings
app.config["SESSION_COOKIE_HTTPONLY"]    = True #js not read session cookie, only browser send it automatically withh request
app.config["SESSION_COOKIE_SAMESITE"]    = "Lax"  #cookie send when user navigate within site, type url directly, cookie not attach if otherr website make a request, 2nd protection of CSRF
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7) #session expire after 7 days
if not IS_DEV:
    app.config["SESSION_COOKIE_SECURE"] = True #cookie send only HTTPS

# db migration login : sqlite to postgres
DATABASE_URL = os.getenv("DATABASE_URL","").strip() # load every line in .env
if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://","postgresql://",1) #url scheme - tells your app which database driver to load
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
    print(f"[DB] PostgreSQL connected: {DATABASE_URL.split('@')[-1]}") 
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///complaints.db"
    print("[DB] SQLite (dev only)")

# Connection pooling configuration : set of db connection kept open & reused 
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True, #flask detect dead connection & open fresh one automatically
    "pool_recycle": 120, #forcibly close and reopen connections after 120 seconds
    "pool_size": 3, #keep 3 connections open permanently 
    "max_overflow": 5 #allow up to 5 more conncetions 
}
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024 # 10,485,760 bytes = 10 MB, max size of incoming request, user uploading massive file show 413 error
db     = SQLAlchemy(app) # connect to flask app, db is db interface object
csrf   = CSRFProtect(app) # activates CSRF protection globally,  stolen CSRF token stays valid forever - trade-off
app.config["WTF_CSRF_TIME_LIMIT"] = None #tokens never expire, default CSRF expire after 1 hr
limiter = Limiter(app=app, key_func=get_remote_address, #count requests per IP address, rate limiter
                  default_limits=["500 per day","100 per hour"],
                  storage_uri=os.getenv("REDIS_URL","memory://")) # store counters

ALLOWED_EXTENSIONS = {"png","jpg","jpeg","gif","webp"}
ALLOWED_AUDIO      = {"wav","mp3","m4a","ogg","webm"}

# CLOUDINARY - Render's free tier has an ephemeral filesystem(every deploy wipes it),seperate cloud service for storing images permenently, with free tier of 25GB
CLOUDINARY_AVAILABLE = False #start as false, if anything goes wrong still runs using local storage instead
# graceful degradation - app works even when an optinal service is unavailable
try:
    import cloudinary, cloudinary.uploader
    #_cn, _ck, _cs - underscore prefix is a Python convention: temporary/private variable
    _cn, _ck, _cs = (os.getenv(x,"").strip() for x in 
                     ["CLOUDINARY_CLOUD_NAME","CLOUDINARY_API_KEY","CLOUDINARY_API_SECRET"]) #generator expression
    if _cn and _ck and _cs:
        cloudinary.config(cloud_name=_cn, api_key=_ck, api_secret=_cs, secure=True)
        CLOUDINARY_AVAILABLE = True
        print(f"[Cloudinary] Ready: {_cn}")
    else:
        print("[Cloudinary] Credentials missing — local storage fallback")
except ImportError:
    print("[Cloudinary] Not installed — local storage fallback")

#it runs only when Cloudinary is unavailable
LOCAL_UPLOAD_FOLDER = "static/uploads"
if not CLOUDINARY_AVAILABLE:
    os.makedirs(LOCAL_UPLOAD_FOLDER, exist_ok=True) 
    #os.makedirs - create folder if not exist, exist_ok=True - dont crash if alredy exist(else FileExistError)

#SMPT - protocol for sending email(Simple Mail Transfer Protocol)
EMAIL_HOST          = "smtp.gmail.com"
EMAIL_PORT          = 587 #standard port for smpt with STARTTLS encryption-starts as a plain connection then upgrades to encrypted
EMAIL_USER          = os.getenv("EMAIL_USER","")
EMAIL_PASSWORD      = os.getenv("EMAIL_PASSWORD","")#google app password-16 character code gmail generatesspecifically for apps
EMAIL_TEMPLATES_DIR = os.path.join("templates","emails")
BASE_URL            = os.getenv("APP_BASE_URL","http://127.0.0.1:5000").rstrip("/") #bug:email template use hardcoded dev tunnel insted of base_url

def _get_state_codes():
    pairs = [
        ("Andhra Pradesh","AP"),("Arunachal Pradesh","AR"),("Assam","AS"),
        ("Bihar","BR"),("Chhattisgarh","CG"),("Goa","GA"),("Gujarat","GJ"),
        ("Haryana","HR"),("Himachal Pradesh","HP"),("Jharkhand","JH"),
        ("Karnataka","KA"),("Kerala","KL"),("Madhya Pradesh","MP"),
        ("Maharashtra","MH"),("Manipur","MN"),("Meghalaya","ML"),
        ("Mizoram","MZ"),("Nagaland","NL"),("Odisha","OD"),("Punjab","PB"),
        ("Rajasthan","RJ"),("Sikkim","SK"),("Tamil Nadu","TN"),("Telangana","TG"),
        ("Tripura","TR"),("Uttar Pradesh","UP"),("Uttarakhand","UK"),
        ("West Bengal","WB"),("Delhi","DL"),("Jammu & Kashmir","JK"),
        ("Ladakh","LA"),("Puducherry","PY"),("Chandigarh","CH"),
    ]
    return {name: os.getenv(f"{ab}_ADMIN_CODE", f"DISABLED_{ab}") for name,ab in pairs}

STATE_ADMIN_CODES = _get_state_codes()
#CACHING:computing something once and storing the result instead of recomputing it every time

#SLA - Service Level Agreement
SLA_DAYS = {
    "Garbage":2,"Road Damage":7,"Streetlight":5,"Water Supply":3,"Drainage":4,
    "Mosquito":3,"Construction":10,"Encroachment":10,"Hoardings":7,"Buildings":7,
    "Tree Cutting":5,"Garden":7,"Air Pollution":5,"Dead Animal":1,"Toilet":3,
    "Food Safety":3,"Stray Cattle":3,"Noise Pollution":3,"Manhole":2,"Dog":3,
    "Tax":14,"Fire":1,"other":7,#(default-7 days submission_date + timedelta(days=SLA_DAYS.get(category, 7))
}

#only 2 status:progress or resolved
VALID_STATUS_MAP = {"progress":"In Progress","resolved":"Resolved"}
#fallback chain(429-rate limit exceeded,404-model not available)
GEMINI_MODELS    = ["gemini-2.5-flash","gemini-2.0-flash-lite","gemini-1.5-flash","gemini-1.5-pro"]

# MODELS, ORM — Object Relational Mapper approch 
class User(db.Model): #base class
    __tablename__ = "users"
    id         = db.Column(db.Integer,     primary_key=True) #PK-uniquely identifies,SQLAlchemy auto-increments
    name       = db.Column(db.String(200), nullable=False) #200 limit, nullable-column never empty defense in depth-if python validation has bug db wont accept data
    email      = db.Column(db.String(200), unique=True, nullable=False) #unique-not two rows are same
    password   = db.Column(db.Text,        nullable=False) #pw hashes are long cross 200 limit text has no length limit
    role       = db.Column(db.String(20),  nullable=False, default="user") #if not select role automatic become user
    state      = db.Column(db.String(100), default="")
    mobile     = db.Column(db.String(20),  default="")
    verified   = db.Column(db.Integer,     default=0) #true/1,false/0(SQLite doesn't have a true Boolean type)
    complaints = db.relationship("Complaint", backref="user", lazy=True) 
    #complaint.user-gives you the User object who filed it, user.complaint-getting all complaint
    #backref="user" creates the reverse direction
    #lazy=True - don't load complaints automatically when you load a user

class Complaint(db.Model):
    __tablename__ = "complaints"
    id           = db.Column(db.Integer,     primary_key=True)
    complaint_id = db.Column(db.String(20),  unique=True, nullable=False, index=True) #index=true : create sepearte sorted array
    #trade-off: indexes take extra disk space and slow down INSERT slightly
    name         = db.Column(db.String(200), nullable=False)
    mobile       = db.Column(db.String(20),  default="")
    category     = db.Column(db.String(100), nullable=False)
    description  = db.Column(db.Text,        nullable=False)
    status       = db.Column(db.String(20),  nullable=False, default="Pending")
    timestamp    = db.Column(db.String(30),  nullable=False)
    deadline     = db.Column(db.String(30),  default="")
    image        = db.Column(db.String(500), default="")
    image_name   = db.Column(db.String(500), default="")
    address      = db.Column(db.Text,        default="")
    latitude     = db.Column(db.String(30),  default="")
    longitude    = db.Column(db.String(30),  default="")
    state        = db.Column(db.String(100), nullable=False, default="", index=True)
    user_id      = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True) #value in this column must exist as an id in the users table

class OTPStore(db.Model):
    __tablename__ = "otp_store"
    id         = db.Column(db.Integer,     primary_key=True)
    email      = db.Column(db.String(200), nullable=False, index=True) #index true - PostgreSQL builds a separate sorted structure
    otp        = db.Column(db.String(10),  nullable=False)
    expires_at = db.Column(db.DateTime,    nullable=False)
    created_at = db.Column(db.DateTime,    default=datetime.utcnow) #utcnow - time changes every sec.
    # if the default value should be the same for every row → write the value. If it should be computed fresh each time → write the function name without parentheses.
    #table is permenent but row is deleted after verification
   
def init_db():
    with app.app_context(): #Flask separates "application context" from "request context
        db.create_all() #create tables by reading all model
        print("[DB] Tables ready.")

# DECORATORS
def login_required(f):
    @wraps(f)
    def d(*a,**k):
        if not session.get("user_id"):
            flash("Please log in.")
            return redirect(url_for("login"))
        return f(*a,**k)
    return d

def admin_required(f):
    @wraps(f)
    def d(*a,**k):
        if session.get("role") != "admin":
            flash("Admin access required.")
            return redirect(url_for("login"))
        return f(*a,**k)
    return d

# FILE HELPERS
def allowed_file(file_bytes, filename):
    ext = filename.rsplit(".",1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_EXTENSIONS and _detect_image_type(file_bytes) in {"png","jpeg","gif","webp"}

def allowed_audio(filename):
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_AUDIO

def save_upload(file):
    fb = file.read()
    if not allowed_file(fb, file.filename):
        print(f"[Upload] Rejected: {file.filename}")
        return ""
        
    if CLOUDINARY_AVAILABLE:
        try:
            r = cloudinary.uploader.upload(fb, folder="municipal_complaints",
                                           resource_type="image", unique_filename=True, overwrite=False)
            url = r.get("secure_url","")
            if url: 
                print(f"[Cloudinary] OK: {url[:60]}...")
                return url
        except Exception as e:
            print(f"[Cloudinary] Failed: {e}. Falling back to local storage.")
            # Do not return "" here, let it slide down to local saving!

    # Local fallback logic if Cloudinary fails or is unavailable
    name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    with open(os.path.join(LOCAL_UPLOAD_FOLDER, name), "wb") as f:
        f.write(fb)
    return name

def generate_complaint_id():
    for _ in range(10):
        cid = "CMP-" + "".join(random.choices(string.ascii_uppercase+string.digits, k=10))
        if not Complaint.query.filter_by(complaint_id=cid).first():
            return cid
    raise RuntimeError("Cannot generate unique complaint ID")

# EMAIL
def _load_template(fn, **kw):
    # render_template_string works with proper Jinja2
    from flask import render_template
    return render_template(f"emails/{fn}", **kw)

def _send_email(to, subj, plain, html=""):
    if not EMAIL_USER or not EMAIL_PASSWORD: return False
    try:
        m = MIMEMultipart("alternative")
        m["Subject"],m["From"],m["To"] = subj,EMAIL_USER,to
        m.attach(MIMEText(plain,"plain"))
        if html: m.attach(MIMEText(html,"html"))
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=15) as s:
            s.starttls(); s.login(EMAIL_USER,EMAIL_PASSWORD); s.sendmail(EMAIL_USER,to,m.as_string())
        print(f"[Email] OK: {subj} → {to}")
        return True
    except Exception as e:
        print(f"[Email] Failed: {e}")
        return False

def _bg(fn,*a,**k):
    threading.Thread(target=fn,args=a,kwargs=k,daemon=True).start()

def send_otp_email(to, otp, name):
    h = _load_template("email_otp.html", name=name, otp=otp, base_url=BASE_URL)
    _bg(_send_email, to, "Email Verification OTP – MunCivic",
        f"Dear {name},\n\nOTP: {otp}\nValid 10 min.\n\n– Muncivic", h)

def send_complaint_confirmation(to, name, cid, cat, desc, dl, ts):
    h = _load_template("email_complaint_registered.html", name=name, complaint_id=cid,
                        category=cat, description=desc, deadline=dl, timestamp=ts, base_url=BASE_URL)
    _bg(_send_email, to, f"Complaint Registered: {cid}",
        f"Dear {name},\n\nID:{cid}\nCategory:{cat}\nDeadline:{dl}\nTrack:{BASE_URL}/track?cid={cid}\n\n– MunCivic", h)

def send_resolution_email(to, name, cid, cat, desc, ts):
    ro = datetime.now().strftime("%d %b %Y, %I:%M %p")
    h  = _load_template("email_complaint_resolved.html", name=name, complaint_id=cid,
                         category=cat, description=desc, timestamp=ts, resolved_on=ro, base_url=BASE_URL)
    _bg(_send_email, to, f"Complaint Resolved: {cid}",
        f"Dear {name},\n\nComplaint {cid} resolved on {ro}.\n\n– MunCivic", h)

def get_sla_info(c):
    sla = SLA_DAYS.get(c.category, 7)
    try:    sub = datetime.strptime(c.timestamp, "%Y-%m-%d %H:%M:%S")
    except: sub = datetime.now()
    dl  = sub + timedelta(days=sla)
    rem = (dl - datetime.now()).days if c.status != "Resolved" else None
    return sla, dl.strftime("%d %b %Y"), rem

def call_gemini(contents):
    last = None
    for m in GEMINI_MODELS:
        try:
            r = genai_client.models.generate_content(model=m, contents=contents)
            return r
        except Exception as e:
            last = e
            if "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e) and "404" not in str(e):
                raise e
    raise last

# ERROR HANDLERS
@app.errorhandler(404)
def not_found(e):  return render_template("404.html"), 404
@app.errorhandler(500)
def srv_err(e):    return render_template("500.html"), 500
@app.errorhandler(413)
def too_big(e):
    flash("File too large. Max 10MB."); return redirect(request.referrer or url_for("submit"))
@app.errorhandler(429)
def rate_lim(e):   return render_template("429.html", message="Too many requests."), 429

# ROUTES
@app.route("/")
def index(): return redirect(url_for("home_page"))

@app.route("/home")
def home_page():
    return render_template("home.html",
        total    = Complaint.query.count(),
        resolved = Complaint.query.filter_by(status="Resolved").count(),
        citizens = User.query.filter_by(role="user").count())

@app.route("/signup", methods=["GET","POST"])
@limiter.limit("10 per hour")
def signup():
    states = list(STATE_ADMIN_CODES.keys())
    if request.method == "POST":
        name,email,password = (request.form.get(x,"").strip() for x in ["name","email","password"])
        email = email.lower()
        mobile,role,state   = (request.form.get(x,"").strip() for x in ["mobile","role","state"])
        if not name or not email or not password:
            return render_template("signup.html", states=states, error="All fields required.")
        if role=="admin" and request.form.get("admin_code","").strip() != STATE_ADMIN_CODES.get(state,""):
            return render_template("signup.html", states=states, error="Invalid Admin Code!")
        if User.query.filter_by(email=email).first():
            return render_template("signup.html", states=states, error="Email already registered.")
        otp = str(random.randint(100000,999999))
        OTPStore.query.filter_by(email=email).delete()
        db.session.add(OTPStore(email=email, otp=otp, expires_at=datetime.utcnow()+timedelta(minutes=10)))
        db.session.commit()
        session["pending_signup"] = {"name":name,"email":email,"password":generate_password_hash(password),
                                     "role":role,"state":state,"mobile":mobile}
        send_otp_email(email, otp, name)
        return redirect(url_for("verify_otp"))
    return render_template("signup.html", states=states)

@app.route("/verify-otp", methods=["GET","POST"])
@limiter.limit("20 per hour")
def verify_otp():
    p = session.get("pending_signup")
    if not p: return redirect(url_for("signup"))
    if request.method == "POST":
        entered,email = request.form.get("otp","").strip(), p["email"]
        rec = OTPStore.query.filter_by(email=email).order_by(OTPStore.created_at.desc()).first()
        if not rec:
            session.pop("pending_signup",None)
            return render_template("signup.html", states=list(STATE_ADMIN_CODES.keys()), error="OTP expired.")
        if datetime.utcnow() > rec.expires_at:
            OTPStore.query.filter_by(email=email).delete(); db.session.commit()
            session.pop("pending_signup",None)
            return render_template("signup.html", states=list(STATE_ADMIN_CODES.keys()), error="OTP expired.")
        if entered == rec.otp:
            try:
                db.session.add(User(name=p["name"],email=p["email"],password=p["password"],
                                    role=p["role"],state=p["state"],mobile=p.get("mobile",""),verified=1))
                OTPStore.query.filter_by(email=email).delete(); db.session.commit()
                session.pop("pending_signup",None)
                flash("Account verified! You can now log in.")
                return redirect(url_for("login")+"?verified=1")
            except Exception as e:
                db.session.rollback()
                return render_template("verifyOTP.html", email=email, error="Account creation failed.")
        return render_template("verifyOTP.html", email=email, error="Wrong OTP!")
    return render_template("verifyOTP.html", email=p["email"])

@app.route("/resend-otp")
@limiter.limit("5 per hour")
def resend_otp():
    p = session.get("pending_signup")
    if not p: return redirect(url_for("signup"))
    email,otp = p["email"], str(random.randint(100000,999999))
    OTPStore.query.filter_by(email=email).delete()
    db.session.add(OTPStore(email=email,otp=otp,expires_at=datetime.utcnow()+timedelta(minutes=10)))
    db.session.commit()
    send_otp_email(email,otp,p["name"])
    return redirect(url_for("verify_otp")+"?resent=1")

@app.route("/login", methods=["GET","POST"])
@limiter.limit("15 per minute")
def login():
    if request.method == "POST":
        email,password = request.form.get("email","").strip().lower(), request.form.get("password","")
        u = User.query.filter_by(email=email).first()
        if u and check_password_hash(u.password, password):
            if not u.verified:
                return render_template("login.html", error="Email not verified.")
            session.permanent = True
            session.update({"user_id":u.id,"user":u.name,"role":u.role,"state":u.state})
            return redirect(url_for("admin") if u.role=="admin" else url_for("home_page"))
        return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html")

@app.route("/submit", methods=["GET","POST"])
@login_required
@limiter.limit("20 per hour")
def submit():
    if request.method == "POST":
        img_val = ""
        f = request.files.get("image")
        if f and f.filename:
            img_val = save_upload(f)
            if not img_val:
                flash("Invalid image. Use PNG, JPG, GIF, or WebP.")
                return render_template("submit.html")
        cid       = generate_complaint_id()
        cat       = request.form.get("category","other")
        desc      = request.form.get("description","").strip()
        ts        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dl_dt     = datetime.strptime(ts,"%Y-%m-%d %H:%M:%S") + timedelta(days=SLA_DAYS.get(cat,7))
        dl_str    = dl_dt.strftime("%d %b %Y")
        ts_disp   = datetime.strptime(ts,"%Y-%m-%d %H:%M:%S").strftime("%d %b %Y, %I:%M %p")
        db.session.add(Complaint(
            complaint_id=cid, name=request.form.get("name","").strip(), category=cat,
            description=desc, status="Pending", timestamp=ts, deadline=dl_str,
            image=img_val, image_name=f.filename if (f and f.filename) else "",
            address=request.form.get("address","").strip(),
            latitude=request.form.get("latitude","").strip(),
            longitude=request.form.get("longitude","").strip(),
            state=session.get("state",""), mobile=request.form.get("mobile","").strip(),
            user_id=session.get("user_id")))
        db.session.commit()
        u = db.session.get(User, session.get("user_id"))
        if u: send_complaint_confirmation(u.email,u.name,cid,cat,desc,dl_str,ts_disp)
        return render_template("submit.html", success_cid=cid)
    return render_template("submit.html")

@app.route("/analyse-complaint", methods=["POST"])
@login_required
@limiter.limit("30 per hour")
def analyse_complaint():
    if not AI_AVAILABLE:
        return jsonify({"error":"AI not available. Set GEMINI_API_KEY."}),503
    try:
        response = None
        if "audio" in request.files:
            audio = request.files["audio"]
            if not audio or not audio.filename: return jsonify({"error":"No audio"}),400
            ab   = audio.read(); mt = audio.mimetype or "audio/webm"
            ext  = {"audio/wav":".wav","audio/webm":".webm","audio/ogg":".ogg",
                    "audio/mp3":".mp3","audio/mpeg":".mp3","audio/m4a":".m4a","audio/mp4":".m4a"}.get(mt,".webm")
            with tempfile.NamedTemporaryFile(delete=False,suffix=ext) as tmp:
                tmp.write(ab); tp=tmp.name
            try: uf = genai_client.files.upload(file=tp, config=genai_types.UploadFileConfig(mime_type=mt))
            finally: os.unlink(tp)
            response = call_gemini([
                "User reports municipal issue in India. Language: Marathi/Hindi/English.\n"
                "1.Detect 2.Translate 3.Category from[Garbage,Road Damage,Streetlight,Water Supply,"
                "Drainage,Mosquito,Construction,Encroachment,Dead Animal,Fire,Manhole,Dog,Other]\n"
                'Return ONLY JSON: {"category":"...","description":"..."}', uf])
        elif "image" in request.files:
            img = request.files["image"]
            if not img or not img.filename: return jsonify({"error":"No image"}),400
            ib   = img.read(); rm = img.mimetype or ""
            e2m  = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp","gif":"image/gif"}
            mt   = rm if rm and rm not in ("application/octet-stream","") else e2m.get((img.filename or "").rsplit(".",1)[-1].lower(),"")
            if not mt or mt=="application/octet-stream":
                h=ib[:12]
                if h[:8]==b'\x89PNG\r\n\x1a\n': mt="image/png"
                elif h[:3]==b'\xff\xd8\xff':    mt="image/jpeg"
                elif h[:4]==b'RIFF' and h[8:12]==b'WEBP': mt="image/webp"
                elif h[:6] in(b'GIF87a',b'GIF89a'): mt="image/gif"
                else: mt="image/jpeg"
            response = call_gemini([
                "Analyze municipal issue image.\n"
                "1.Category from[Garbage,Road Damage,Streetlight,Water Supply,Drainage,Mosquito,"
                "Construction,Encroachment,Dead Animal,Fire,Manhole,Dog,Other]\n"
                '2.One-sentence description.\nReturn ONLY JSON: {"category":"...","description":"..."}',
                genai_types.Part.from_bytes(data=ib, mime_type=mt)])
        else:
            return jsonify({"error":"No file"}),400
        raw = (response.text or "").strip().replace("```json","").replace("```","").strip()
        try:    data = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
        except: data = {"category":"Other","description":raw[:200]}
        return jsonify(data)
    except Exception as e:
        print(f"[AI] {e}"); return jsonify({"error":str(e)}),500

@app.route("/track", methods=["GET","POST"])
def track():
    complaint=deadline_str=remaining_days=None; sla_days=0
    image_url = None
    cid = (request.form.get("cid","") if request.method=="POST" else request.args.get("cid","")).strip().upper()
    if cid:
        complaint = Complaint.query.filter_by(complaint_id=cid).first()
        if complaint: 
            sla_days,deadline_str,remaining_days = get_sla_info(complaint)
            if complaint.image:
                if complaint.image.startswith("http"):
                    image_url = complaint.image
                else:
                    image_url = url_for('static',filename='uploads/' + complaint.image)
    return render_template("track.html", complaint=complaint, cid=cid,
                           sla_days=sla_days, remaining_days=remaining_days, deadline_str=deadline_str, image_url=image_url)

@app.route("/admin")
@admin_required
def admin():
    state,search = session.get("state",""), request.args.get("search","").strip()
    q = Complaint.query.filter_by(state=state)
    if search:
        like=f"%{search}%"
        q=q.filter(db.or_(Complaint.complaint_id.ilike(like),Complaint.name.ilike(like),
                           Complaint.category.ilike(like),Complaint.status.ilike(like),
                           Complaint.description.ilike(like),Complaint.mobile.ilike(like)))
    rows = q.order_by(Complaint.timestamp.desc()).all()
    def sk(c):
        if c.status=="Resolved": return(1,9999)
        try: sub=datetime.strptime(c.timestamp,"%Y-%m-%d %H:%M:%S")
        except: sub=datetime.now()
        return(0,(sub+timedelta(days=SLA_DAYS.get(c.category,7))-datetime.now()).days)
    data=[]
    for c in sorted(rows,key=sk):
        image_url = None
        if c.image:
            if c.image.startswith("http"):
                image_url = c.image
            else:
                image_url = url_for('static',filename='uploads/' + c.image)
        print(f"[DEBUG] complaint_id={c.complaint_id} | c.image={c.image!r} | image_url={image_url!r}")
        _,ds,rem=get_sla_info(c); data.append({"c":c,"deadline_str":ds,"remaining":rem,"image_url":image_url})
    return render_template("admin.html", data=data, admin_state=state, search=search,
        total    = Complaint.query.filter_by(state=state).count(),
        pending  = Complaint.query.filter_by(state=state,status="Pending").count(),
        progress = Complaint.query.filter_by(state=state,status="In Progress").count(),
        resolved = Complaint.query.filter_by(state=state,status="Resolved").count())

@app.route("/update/<int:id>/<string:status>", methods=["GET","POST"])
@admin_required
def update_status(id, status):
    ns = VALID_STATUS_MAP.get(status)
    if not ns: return f"Invalid status: {status}",400
    c  = Complaint.query.filter_by(id=id, state=session.get("state","")).first()
    if not c: return "Not found or access denied.",403
    c.status = ns; db.session.commit()
    if ns == "Resolved":
        r = c.user or User.query.filter_by(name=c.name,role="user").first()
        if r:
            try: ts=datetime.strptime(c.timestamp,"%Y-%m-%d %H:%M:%S").strftime("%d %b %Y, %I:%M %p")
            except: ts=c.timestamp
            send_resolution_email(r.email,c.name,c.complaint_id,c.category,c.description,ts)
    return redirect(url_for("admin"))

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("home_page"))

@app.route("/robots.txt")
def robots():
    return("User-agent: *\nDisallow: /admin\nDisallow: /submit\nDisallow: /verify-otp\n",
           200,{"Content-Type":"text/plain"})

init_db()
if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_ENV") == "development",
            host="0.0.0.0", port=int(os.getenv("PORT",5000)))