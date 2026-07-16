import os
import re
import time
import logging
import pathlib
import asyncio
import tempfile
import aiohttp
from typing import Dict, Any, Optional, Tuple

# Telethon Imports
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio, Message

# Mutagen Metadata Imports
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, error as ID3Error
from mutagen.mp4 import MP4, MP4Cover

# --- लॉगिंग कॉन्फिगरेशन ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TaggerBot")

# --- एनवायरनमेंट वेरिएबल्स ---
API_ID = 34801155
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8"
BOT_TOKEN = "8918721301:AAGQomTKJ5vtViPRyAhHAZ51_eEmJk1v25I"

# --- कॉन्स्टेंट्स ---
ARTIST_NAME = "@AllstoryFM2"
STATE_TTL_SECONDS = 600  

# --- इन-मेमोरी स्टेट मैनेजर और टास्क क्यू ---
pending_files: Dict[int, Dict[str, Any]] = {}
task_queue: asyncio.Queue = asyncio.Queue()  # सभी टास्क को लाइन में रखने के लिए

# --- नेटिव एसिंक्रोनस हेल्थ चेक वेब सर्वर ---
from aiohttp import web

async def health_check_handler(request: web.Request) -> web.Response:
    return web.Response(text="Bot is alive and running successfully!", content_type="text/plain")

async def start_health_server() -> None:
    app = web.Application()
    app.router.add_get('/', health_check_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🖥️ Native Async Health check server active on port {port}")

# --- बैकग्राउंड रैम क्लीनर टास्क ---
async def track_and_expire_states() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired_chats = [
            chat_id for chat_id, state in pending_files.items()
            if now - state.get("timestamp", 0) > STATE_TTL_SECONDS
        ]
        for chat_id in expired_chats:
            pending_files.pop(chat_id, None)

# --- हेल्पर फंक्शन्स ---
def sanitize_filename(filename: str) -> str:
    name = os.path.basename(filename)
    return re.sub(r'[\\/*?:"<>|]', "", name)

def extract_episode_number(filename: str, caption: str = "") -> str:
    match = re.search(r'(?:ep|episode|story)[-_\s]*(\d+)', filename, re.IGNORECASE)
    if match: return match.group(1)
    if caption:
        match_cap = re.search(r'(?:ep|episode|story)[-_\s]*(\d+)', caption, re.IGNORECASE)
        if match_cap: return match_cap.group(1)
    fallback = re.search(r'\d+', filename)
    if fallback: return fallback.group()
    if caption:
        fallback_cap = re.search(r'\d+', caption)
        if fallback_cap: return fallback_cap.group()
    return "Unknown"

def get_image_mime_and_format(data: bytes) -> Tuple[str, int]:
    if data.startswith(b'\xff\xd8'): return 'image/jpeg', MP4Cover.FORMAT_JPEG
    elif data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png', MP4Cover.FORMAT_PNG
    return 'image/jpeg', MP4Cover.FORMAT_JPEG

def get_audio_duration(file_path: str, ext: str) -> int:
    try:
        if ext == '.mp3': return int(MP3(file_path).info.length)
        elif ext == '.m4a': return int(MP4(file_path).info.length)
    except Exception as e:
        logger.error(f"Failed to read audio duration: {e}")
    return 0

# --- इमेज डाउनलोडर ---
async def download_image_from_url(url: str) -> Optional[bytes]:
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200: return None
                content_type = response.headers.get('content-type', '').lower()
                if 'image' not in content_type: return None
                return await response.read()
    except Exception as e:
        logger.error(f"Error fetching direct image URL: {e}")
    return None

async def download_image_from_tg(client: TelegramClient, url: str) -> Optional[bytes]:
    match = re.match(r'https?://t\.me/(?:c/)?([^/]+)/(\d+)', url)
    if not match: return None
    try:
        channel_ref = match.group(1)
        message_id = int(match.group(2))
        if channel_ref.isdigit():
            channel_ref = int(f"-100{channel_ref}") if not channel_ref.startswith("-100") else int(channel_ref)
        try: entity = await client.get_entity(channel_ref)
        except Exception: entity = channel_ref
            
        msg = await client.get_messages(entity, ids=message_id)
        if not msg: return None
        if msg.photo: return await client.download_media(msg.photo, bytes)
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
            return await client.download_media(msg.document, bytes)
        elif msg.media and hasattr(msg.media, 'webpage') and msg.media.webpage and getattr(msg.media.webpage, 'photo', None):
            return await client.download_media(msg.media.webpage.photo, bytes)
    except Exception as e:
        logger.error(f"Error resolving Telegram link: {e}")
    return None

# --- मेटाडेटा इंजन ---
def process_mp3_metadata(file_path: str, title: str, artist: str, album: str, image_data: bytes) -> bool:
    try:
        try: audio = MP3(file_path, ID3=ID3)
        except ID3Error:
            audio = MP3(file_path)
            audio.add_tags()
        if audio.tags is None: audio.add_tags()
        for tag in ["TIT2", "TPE1", "TALB"]: audio.tags.delall(tag)
        keys_to_delete = [k for k in audio.tags.keys() if k.startswith("APIC")]
        for key in keys_to_delete: audio.tags.pop(key, None)
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(TALB(encoding=3, text=album))
        mime_type, _ = get_image_mime_and_format(image_data)
        audio.tags.add(APIC(encoding=3, mime=mime_type, type=3, desc=u'Cover', data=image_data))
        audio.save()
        return True
    except Exception as e:
        logger.error(f"Error processing MP3 metadata: {e}")
        return False

def process_m4a_metadata(file_path: str, title: str, artist: str, album: str, image_data: bytes) -> bool:
    try:
        audio = MP4(file_path)
        audio["\xa9nam"] = [title]
        audio["\xa9ART"] = [artist]
        audio["\xa9alb"] = [album]
        _, img_format = get_image_mime_and_format(image_data)
        audio["covr"] = [MP4Cover(image_data, imageformat=img_format)]
        audio.save()
        return True
    except Exception as e:
        logger.error(f"Error processing M4A metadata: {e}")
        return False

# --- मुख्य क्लाइंट इनिशियलाइजेशन ---
bot = TelegramClient('tagger_bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(incoming=True, pattern='/start'))
async def start_handler(event: events.NewMessage.Event) -> None:
    await event.respond("👋 **नमस्ते! मैं आपका लाइन-बाय-लाइन ऑटोमैटिक ऑडियो टैगर बॉट हूँ।**\n\nअब आप एक साथ कई फाइल्स भेज सकते हैं, मैं उन्हें बिल्कुल सही क्रम में ही अपलोड करूँगा!")

@bot.on(events.NewMessage(incoming=True))
async def incoming_message_handler(event: events.NewMessage.Event) -> None:
    message: Message = event.message
    chat_id = event.chat_id
    
    if message.text and message.text.startswith('/start'): return

    if message.file and message.file.ext.lower() in ['.mp3', '.m4a']:
        raw_name = message.file.name or f"audio{message.file.ext}"
        file_name = sanitize_filename(raw_name)
        caption_text = message.message or ""
        
        url_match = re.search(r'(https?://[^\s]+)', caption_text)
        if url_match:
            # सीधे टास्क को Queue में डालें ताकि लाइन न टूटे
            await task_queue.put((event, message.media, file_name, url_match.group(1), caption_text))
            return
            
        pending_files[chat_id] = {"media": message.media, "file_name": file_name, "timestamp": time.time()}
        await event.respond("📥 **ऑडियो फाइल मिल गई!** अब फोटो का लिंक भेजें।")
        return

    elif message.text and not message.text.startswith('/'):
        input_url = message.text.strip()
        url_match = re.search(r'(https?://[^\s]+)', input_url)
        if not url_match: return
            
        if chat_id in pending_files:
            file_data = pending_files.pop(chat_id)
            await task_queue.put((event, file_data["media"], file_data["file_name"], url_match.group(1), ""))
        else:
            await event.respond("ℹ️ **पहले मुझे एक ऑडियो फाइल भेजें**।")

# --- क्यू वर्कर (यह एक-एक करके ही प्रोसेस करेगा) ---
async def queue_worker() -> None:
    """यह बैकग्राउंड में चलता रहेगा और फाइल्स को एक-एक करके लाइन से प्रोसेस करेगा"""
    while True:
        event, file_media, file_name, image_url, caption_text = await task_queue.get()
        try:
            await pipeline_process_and_send(event, file_media, file_name, image_url, caption_text)
        except Exception as e:
            logger.error(f"Error in worker: {e}")
        finally:
            task_queue.task_done()

# --- मुख्य प्रोसेसिंग पाइपलाइन ---
async def pipeline_process_and_send(event: events.NewMessage.Event, file_media: Any, file_name: str, image_url: str, caption_text: str) -> None:
    chat_id = event.chat_id
    status_msg = await event.respond(f"⏳ **लाइन में आपका नंबर आ गया है: {file_name} की प्रोसेसिंग शुरू...**")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        local_audio_path = os.path.join(temp_dir, file_name)
        try:
            await status_msg.edit("📥 **फ़ाइल डाउनलोड की जा रही है...**")
            await bot.download_media(file_media, local_audio_path)
            
            ep_num = extract_episode_number(file_name, caption_text)
            title = f"Ep {ep_num}"
            album = f"Ep {ep_num} - Single"
            
            await status_msg.edit("🖼️ **कवर इमेज डाउनलोड की जा रही है...**")
            image_data = await download_image_from_tg(bot, image_url)
            if not image_data:
                image_data = await download_image_from_url(image_url)
                
            if not image_data:
                await status_msg.edit(f"❌ **{file_name} के लिए फोटो डाउनलोड नहीं हो सकी।**")
                return

            await status_msg.edit("✍️ **ऑडियो में डेटा डाला जा रहा है...**")
            ext = os.path.splitext(file_name)[1].lower()
            
            success = False
            if ext == '.mp3':
                success = await asyncio.to_thread(process_mp3_metadata, local_audio_path, title, ARTIST_NAME, album, image_data)
            elif ext == '.m4a':
                success = await asyncio.to_thread(process_m4a_metadata, local_audio_path, title, ARTIST_NAME, album, image_data)
                
            if not success:
                await status_msg.edit("❌ **मेटाडेटा राइटिंग एरर!**")
                return
                
            await status_msg.edit("📤 **अपलोड किया जा रहा है...**")
            duration = get_audio_duration(local_audio_path, ext)
            audio_attributes = [DocumentAttributeAudio(duration=duration, title=title, performer=ARTIST_NAME)]
            
            thumb_path = os.path.join(temp_dir, "thumb.jpg")
            with open(thumb_path, "wb") as f: f.write(image_data)
            
            await bot.send_file(
                chat_id, local_audio_path,
                caption=f"✅ **सफलतापूर्वक अपडेट किया गया!**\n\n📌 **Title:** {title}\n🎤 **Artist:** {ARTIST_NAME}",
                attributes=audio_attributes, thumb=thumb_path, supports_streaming=True
            )
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit(f"❌ **खराबी आई:** `{str(e)}`")

async def main() -> None:
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("🚀 Bot authenticated successfully.")
    await start_health_server()
    asyncio.create_task(track_and_expire_states())
    
    # क्यू वर्कर को बैकग्राउंड में चालू करें
    asyncio.create_task(queue_worker())
    
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): logger.info("Bot stopped.")
    
