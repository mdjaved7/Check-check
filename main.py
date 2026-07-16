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

# --- एनवायरनमेंट वेरिएबल्स की जांच ---
API_ID = 34801155
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8"
BOT_TOKEN = "8918721301:AAGQomTKJ5vtViPRyAhHAZ51_eEmJk1v25I"

# (चेकिंग वाला कोड हटा दिया गया है क्योंकि वैल्यू सीधे दे दी गई हैं)


# --- कॉन्स्टेंट्स (Constants) ---
ARTIST_NAME = "@AllstoryFM2"
STATE_TTL_SECONDS = 600  # 10 मिनट बाद रैम से पेंडिंग रिक्वेस्ट डिलीट हो जाएगी

# --- इन-मेमोरी स्टेट मैनेजर (RAM Storage) ---
pending_files: Dict[int, Dict[str, Any]] = {}

# --- नेटिव एसिंक्रोनस हेल्थ चेक वेब सर्वर (Railway / Render के लिए) ---
from aiohttp import web

async def health_check_handler(request: web.Request) -> web.Response:
    return web.Response(text="Bot is alive and running successfully!", content_type="text/plain")

async def start_health_server() -> None:
    """चैनल के पोर्ट को बाइंड करने के लिए एसिंक्रोनस सर्वर शुरू करता है"""
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
    """मेमोरी लीक से बचने के लिए पुराने पेंडिंग रिक्वेस्ट्स को साफ़ करता है"""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired_chats = [
            chat_id for chat_id, state in pending_files.items()
            if now - state.get("timestamp", 0) > STATE_TTL_SECONDS
        ]
        for chat_id in expired_chats:
            pending_files.pop(chat_id, None)
            logger.info(f"🧹 Evicted expired image waiting state for chat_id: {chat_id}")

# --- हेल्पर फंक्शन्स ---
def sanitize_filename(filename: str) -> str:
    """फाइल के नाम से खतरनाक कैरेक्टर्स और पाथ ट्रैवर्सल को हटाता है"""
    name = os.path.basename(filename)
    return re.sub(r'[\\/*?:"<>|]', "", name)

def extract_episode_number(filename: str) -> str:
    """फाइल नाम से एपिसोड नंबर खोजने के लिए उन्नत रेगेक्स"""
    match = re.search(r'(?:ep|episode|story)[-_\s]*(\d+)', filename, re.IGNORECASE)
    if match:
        return match.group(1)
    fallback = re.search(r'\d+', filename)
    if fallback:
        return fallback.group()
    return "Unknown"

def get_image_mime_and_format(data: bytes) -> Tuple[str, int]:
    """इमेज बाइट्स से उसका सही फॉर्मेट और माइम-टाइप पता लगाता है"""
    if data.startswith(b'\xff\xd8'):
        return 'image/jpeg', MP4Cover.FORMAT_JPEG
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', MP4Cover.FORMAT_PNG
    return 'image/jpeg', MP4Cover.FORMAT_JPEG

def get_audio_duration(file_path: str, ext: str) -> int:
    """ऑडियो फाइल की वास्तविक समय अवधि (Duration) निकालता है"""
    try:
        if ext == '.mp3':
            return int(MP3(file_path).info.length)
        elif ext == '.m4a':
            return int(MP4(file_path).info.length)
    except Exception as e:
        logger.error(f"Failed to read audio duration: {e}")
    return 0

# --- इमेज डाउनलोडर (एसिंक्रोनस) ---
async def download_image_from_url(url: str) -> Optional[bytes]:
    """वेब URL से इमेज डाउनलोड करता है (HTML पेजों को रिजेक्ट करता है)"""
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch image from URL. Status: {response.status}")
                    return None
                
                content_type = response.headers.get('content-type', '').lower()
                if 'image' not in content_type:
                    logger.warning(f"Rejected URL download: Content-Type '{content_type}' is not an image.")
                    return None
                
                return await response.read()
    except Exception as e:
        logger.error(f"Error fetching direct image URL: {e}", exc_info=True)
    return None

async def download_image_from_tg(client: TelegramClient, url: str) -> Optional[bytes]:
    """टेलीग्राम पोस्ट लिंक (पब्लिक/प्राइवेट एडमिन चैनल) से इमेज निकालता है"""
    match = re.match(r'https?://t\.me/(?:c/)?([^/]+)/(\d+)', url)
    if not match:
        return None
    try:
        channel_ref = match.group(1)
        message_id = int(match.group(2))
        
        if channel_ref.isdigit():
            channel_ref = int(f"-100{channel_ref}") if not channel_ref.startswith("-100") else int(channel_ref)
            
        try:
            entity = await client.get_entity(channel_ref)
        except Exception:
            entity = channel_ref
            
        msg = await client.get_messages(entity, ids=message_id)
        if not msg:
            return None
            
        if msg.photo:
            return await client.download_media(msg.photo, bytes)
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
            return await client.download_media(msg.document, bytes)
        elif msg.media and hasattr(msg.media, 'webpage') and msg.media.webpage and getattr(msg.media.webpage, 'photo', None):
            return await client.download_media(msg.media.webpage.photo, bytes)
            
    except Exception as e:
        logger.error(f"Error resolving Telegram link: {e}", exc_info=True)
    return None

# --- मेटाडेटा इंजन (Mutagen Engine) ---
def process_mp3_metadata(file_path: str, title: str, artist: str, album: str, image_data: bytes) -> bool:
    """MP3 फाइल के पुराने मेटाडेटा को साफ़ करके नया डेटा लिखता है"""
    try:
        try:
            audio = MP3(file_path, ID3=ID3)
        except ID3Error:
            audio = MP3(file_path)
            audio.add_tags()
            
        if audio.tags is None:
            audio.add_tags()
            
        # पुराने डुप्लिकेट फ्रेम हटाएँ
        for tag in ["TIT2", "TPE1", "TALB"]:
            audio.tags.delall(tag)
        
        keys_to_delete = [k for k in audio.tags.keys() if k.startswith("APIC")]
        for key in keys_to_delete:
            audio.tags.pop(key, None)
            
        # नए फ़्रेम जोड़ें
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(TALB(encoding=3, text=album))
        
        mime_type, _ = get_image_mime_and_format(image_data)
        audio.tags.add(APIC(
            encoding=3,
            mime=mime_type,
            type=3,  # Front cover
            desc=u'Cover',
            data=image_data
        ))
        audio.save()
        return True
    except Exception as e:
        logger.error(f"Error processing MP3 structural metadata: {e}", exc_info=True)
        return False

def process_m4a_metadata(file_path: str, title: str, artist: str, album: str, image_data: bytes) -> bool:
    """M4A फाइल के मेटाडेटा को सुरक्षित रूप से ओवरराइट करता है"""
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
        logger.error(f"Error processing M4A structural metadata: {e}", exc_info=True)
        return False

# --- मुख्य क्लाइंट इनिशियलाइजेशन ---
bot = TelegramClient('tagger_bot_session', API_ID, API_HASH)

# --- टेलीग्राम इवेंट हैंडलर्स ---
@bot.on(events.NewMessage(incoming=True, pattern='/start'))
async def start_handler(event: events.NewMessage.Event) -> None:
    logger.info(f"User {event.sender_id} triggered /start command.")
    await event.respond(
        "👋 **नमस्ते! मैं एक प्रोडक्शन-ग्रेड सुपर-फास्ट ऑटोमैटिक ऑडियो टैगर बॉट हूँ।**\n\n"
        "**काम करने का तरीका:**\n"
        "1. सबसे पहले अपनी **MP3** या **M4A** फाइल भेजें।\n"
        "2. फाइल के तुरंत बाद **फोटो का लिंक (URL)** भेजें।\n\n"
        "*नोट: मैं टेलीग्राम चैनल के पोस्ट लिंक और डायरेक्ट इमेज लिंक्स दोनों सपोर्ट करता हूँ!*"
    )

@bot.on(events.NewMessage(incoming=True))
async def incoming_message_handler(event: events.NewMessage.Event) -> None:
    message: Message = event.message
    chat_id = event.chat_id
    user_id = event.sender_id or chat_id
    
    if message.text and message.text.startswith('/start'):
        return

    # परिदृश्य 1: यूजर ने ऑडियो फाइल भेजी है
    if message.file and message.file.ext.lower() in ['.mp3', '.m4a']:
        raw_name = message.file.name or f"audio{message.file.ext}"
        file_name = sanitize_filename(raw_name)
        caption_text = message.message
        
        logger.info(f"User {user_id} sent an audio file: {file_name}")
        
        # यदि लिंक पहले से ही कैप्शन में मौजूद हो
        if caption_text and re.match(r'^https?://', caption_text.strip()):
            await pipeline_process_and_send(event, message.media, file_name, caption_text.strip())
            return
            
        pending_files[chat_id] = {
            "media": message.media,
            "file_name": file_name,
            "timestamp": time.time()
        }
        await event.respond("📥 **ऑडियो फाइल मिल गई!** अब इस फाइल में जोड़ने के लिए **फोटो का लिंक (URL)** भेजें।")
        return

    # परिदृश्य 2: यूजर ने फोटो का यूआरएल (टेक्स्ट) भेजा है
    elif message.text and not message.text.startswith('/'):
        input_url = message.text.strip()
        if not re.match(r'^https?://', input_url):
            return  # वैलिड यूआरएल नहीं है तो अनदेखा करें
            
        if chat_id in pending_files:
            file_data = pending_files.pop(chat_id) # रैम से डेटा निकालें
            logger.info(f"User {user_id} sent image URL link for active queued file: {file_data['file_name']}")
            await pipeline_process_and_send(event, file_data["media"], file_data["file_name"], input_url)
        else:
            await event.respond("ℹ️ **पहले मुझे एक ऑडियो फाइल (MP3/M4A) भेजें**, उसके बाद यह फोटो लिंक काम करेगा।")

# --- मुख्य प्रोसेसिंग पाइपलाइन ---
async def pipeline_process_and_send(event: events.NewMessage.Event, file_media: Any, file_name: str, image_url: str) -> None:
    chat_id = event.chat_id
    start_time = time.time()
    status_msg = await event.respond("⚡ **प्रोसेसिंग शुरू हो रही है...**")
    
    # सुरक्षित अस्थायी निर्देशिका (Temporary Directory) बनाना
    with tempfile.TemporaryDirectory() as temp_dir:
        local_audio_path = os.path.join(temp_dir, file_name)
        
        try:
            # 1. ऑडियो फाइल डाउनलोड करें
            await status_msg.edit("📥 **फ़ाइल डाउनलोड की जा रही है...** (कृपया धीरज रखें)")
            await bot.download_media(file_media, local_audio_path)
            
            # 2. फाइल नाम से एपिसोड नंबर निकालें
            ep_num = extract_episode_number(file_name)
            title = f"Ep {ep_num}"
            album = f"Ep {ep_num} - Single"
            
            # 3. इमेज डाउनलोड करें (पहले टेलीग्राम लिंक चेक करें, फिर डायरेक्ट यूआरएल)
            await status_msg.edit("🖼️ **कवर इमेज डाउनलोड की जा रही है...**")
            image_data = await download_image_from_tg(bot, image_url)
            if not image_data:
                image_data = await download_image_from_url(image_url)
                
            if not image_data:
                await status_msg.edit("❌ **फोटो डाउनलोड करने में विफलता!** कृपया पक्का करें कि लिंक सही है और वह कोई वैलिड इमेज ही है।")
                return

            # 4. मेटाडेटा एडिटिंग और कवर आर्ट जोड़ना
            await status_msg.edit("✍️ **ऑडियो में नया मेटाडेटा और कवर डाला जा रहा है...**")
            ext = os.path.splitext(file_name)[1].lower()
            
            success = False
            if ext == '.mp3':
                success = await asyncio.to_thread(process_mp3_metadata, local_audio_path, title, ARTIST_NAME, album, image_data)
            elif ext == '.m4a':
                success = await asyncio.to_thread(process_m4a_metadata, local_audio_path, title, ARTIST_NAME, album, image_data)
                
            if not success:
                await status_msg.edit("❌ **मेटाडेटा राइटिंग एरर!** फ़ाइल को प्रोसेस नहीं किया जा सका।")
                return
                
            # 5. वास्तविक ड्यूरेशन निकालें और फाइल वापस भेजें
            await status_msg.edit("📤 **अपडेटेड फाइल तैयार है! टेलीग्राम पर अपलोड की जा रही है...**")
            duration = get_audio_duration(local_audio_path, ext)
            
            audio_attributes = [
                DocumentAttributeAudio(
                    duration=duration,
                    title=title,
                    performer=ARTIST_NAME
                )
            ]
            
            # अस्थायी रूप से थंबनेल इमेज फाइल बनाएँ ताकि टेलीग्राम उसे ऑडियो प्रीव्यू में दिखाए
            thumb_path = os.path.join(temp_dir, "thumb.jpg")
            with open(thumb_path, "wb") as f:
                f.write(image_data)
            
            await bot.send_file(
                chat_id,
                local_audio_path,
                caption=f"✅ **सफलतापूर्वक अपडेट किया गया!**\n\n📌 **Title:** {title}\n🎤 **Artist:** {ARTIST_NAME}\n⏱️ **Duration:** {duration}s",
                attributes=audio_attributes,
                thumb=thumb_path,
                supports_streaming=True
            )
            await status_msg.delete()
            logger.info(f"Successfully processed and sent {file_name} in {time.time() - start_time:.2f}s")

        except Exception as e:
            logger.error(f"Critical error encountered during processing workflow: {e}", exc_info=True)
            await status_msg.edit(f"❌ **एक अप्रत्याशित खराबी आई:** `{str(e)}`")

# --- मुख्य स्टार्टअप स्क्रिप्ट ---
async def main() -> None:
    # 1. क्लाइंट शुरू करें
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("🚀 Bot has successfully authenticated with Telegram Server.")
    
    # 2. क्लाउड के लिए हेल्थ-चेक वेब सर्वर शुरू करें
    await start_health_server()
    
    # 3. रैम क्लीनर बैकग्राउंड टास्क चालू करें
    asyncio.create_task(track_and_expire_states())
    
    # 4. बोट को रनिंग मोड में रखें
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped gracefully.")
            
