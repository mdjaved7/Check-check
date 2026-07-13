import logging
import json
import os
import time
import asyncio
import urllib.request

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, PeerIdInvalid, ChatAdminRequired, MessageNotModified

# ---- AUDIO METADATA LIBRARY ----
try:
    # TPE1 = Performer/Artist Tag, TIT2 = Title Tag
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, APIC, error, TPE1, TIT2
except ImportError:
    print("Mutagen library missing! Terminal mein run karein: pip install mutagen")
    exit()

# ---- CONFIGURATION ----
API_ID = 34801155          
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8" 
BOT_TOKEN = "8961976960:AAHClYL_3wisXbO3eYsnISMe3xAF5Js0hL8"
OWNER_ID = 6598432032

# ✨ यहाँ वो नाम सेट करें जो ऑडियो के टाइटल के नीचे दिखेगा
PERFORMER_NAME = "▣ @AllstoryFM2"

app = Client("ultimate_forward_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

CHANNELS_FILE = "channels_data.json"
STATES_FILE = "user_states_data.json"
file_lock = asyncio.Lock()

active_timers = {}
last_edit_time = {}
pending_status_creation = {}

# ---- ASYNC JSON DATABASE SYSTEM ----
def load_json(file_name, default_value):
    if not os.path.exists(file_name):
        with open(file_name, "w") as f:
            json.dump(default_value, f, indent=4)
        return default_value
    try:
        with open(file_name, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading {file_name}: {e}")
        return default_value

def save_json(file_name, data):
    try:
        with open(file_name, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {file_name}: {e}")

# ---- UNIVERSAL FLOODWAIT WRAPPER ----
async def safe_api_call(func, *args, **kwargs):
    retries = 5
    attempt = 0
    while attempt < retries:
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            logging.warning(f"⏳ FloodWait hit! Sleeping for {e.value} seconds before retrying...")
            await asyncio.sleep(e.value)
            attempt += 1
        except MessageNotModified:
            return True 
        except PeerIdInvalid:
            logging.warning("⚠️ PeerIdInvalid encountered. Refreshing cache (sleeping 2s)...")
            await asyncio.sleep(2)
            attempt += 1
        except Exception as e:
            logging.error(f"❌ API Error in {func.__name__}: {e}")
            return None
    return None

# ---- FILTERS ----
async def owner_filter(_, __, message):
    return message.from_user and message.from_user.id == OWNER_ID

is_owner = filters.create(owner_filter)

# 1. ADD/UPDATE CHANNEL COMMAND
@app.on_message(filters.command("addchannel") & filters.private & is_owner)
async def add_channel_cmd(client, message):
    try:
        args = message.command
        if len(args) < 4:
            await safe_api_call(message.reply_text, "❌ **Naya Format:** `/addchannel <Channel_Name> <Channel_ID> <Image_Link>`")
            return
        
        ch_name = args[1]
        try:
            ch_id = int(args[2])
        except ValueError:
            await safe_api_call(message.reply_text, "❌ Channel ID hamesha ek number hona chahiye!")
            return
            
        cover_url = args[3]
        if not cover_url.startswith("http"):
            await safe_api_call(message.reply_text, "❌ Image link valid nahi hai! Link hamesha 'http' ya 'https' se shuru hona chahiye.")
            return

        async with file_lock:
            try:
                member = await client.get_chat_member(ch_id, "me")
                if not member.privileges or not member.privileges.can_post_messages:
                    await safe_api_call(message.reply_text, "⚠️ Bot channel mein toh hai, par uske paas **Post Messages** ki permission nahi hai!")
                    return
            except ChatAdminRequired:
                await safe_api_call(message.reply_text, "❌ Bot ko us channel mein Admin banana zaroori hai!")
                return
            except Exception as e:
                await safe_api_call(message.reply_text, f"❌ Verification Fail: User/Bot ko channel nahi mila. Error: {e}")
                return

            channels_dict = load_json(CHANNELS_FILE, {})
            channels_dict[ch_name] = {
                "id": ch_id,
                "cover_url": cover_url
            }
            save_json(CHANNELS_FILE, channels_dict)
        
        await safe_api_call(message.reply_text, f"✅ **Channel & Cover Photo Saved!**\nName: {ch_name}\nID: `{ch_id}`\n🖼️ Photo Link Attached!")
    except Exception as e:
        await safe_api_call(message.reply_text, f"❌ Error: {str(e)}")

# 2. REMOVE CHANNEL COMMAND
@app.on_message(filters.command("removechannel") & filters.private & is_owner)
async def remove_channel_cmd(client, message):
    args = message.command
    if len(args) < 2:
        await safe_api_call(message.reply_text, "❌ **Format:** `/removechannel <Channel_Name>`")
        return
    ch_name = args[1]
    
    async with file_lock:
        channels_dict = load_json(CHANNELS_FILE, {})
        if ch_name in channels_dict:
            del channels_dict[ch_name]
            save_json(CHANNELS_FILE, channels_dict)
            await safe_api_call(message.reply_text, f"🗑️ Channel `{ch_name}` ko database se hata diya gaya hai.")
        else:
            await safe_api_call(message.reply_text, "❌ Is naam ka koi channel database mein nahi mila.")

# 3. LIST CHANNELS COMMAND
@app.on_message(filters.command("listchannels") & filters.private & is_owner)
async def list_channels_cmd(client, message):
    channels_dict = load_json(CHANNELS_FILE, {})
    if not channels_dict:
        await safe_api_call(message.reply_text, "📂 Database abhi khali hai.")
        return
    res = "📋 **Saved Channels List:**\n\n"
    for name, data in channels_dict.items():
        if isinstance(data, dict):
            res += f"🔹 **{name}**: `{data['id']}` [🖼️ Photo Set ✅]\n"
        else:
            res += f"🔹 **{name}**: `{data}` [❌ Photo Not Set]\n"
    await safe_api_call(message.reply_text, res)

# 4. START COMMAND
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    user_id = str(message.from_user.id) 
    
    if message.from_user.id != OWNER_ID:
        await safe_api_call(message.reply_text, "❌ **Sorry!** Aap is bot ke admin/owner nahi hain. Aap is bot ko use nahi kar sakte.")
        return
    
    async with file_lock:
        user_states = load_json(STATES_FILE, {})
        
        if user_id in user_states and user_states[user_id].get("files"):
            count = len(user_states[user_id]["files"])
            keyboard = [
                [InlineKeyboardButton("Purani Queue Use Karein 🔄", callback_data="choose_ch")],
                [InlineKeyboardButton("Nayi Queue Banayein (Clear) 🗑️", callback_data="clear_and_start")]
            ]
            await safe_api_call(
                message.reply_text,
                f"⚠️ Aapki queue mein pehle se **{count} files** pending hain! Aap kya karna chahte hain?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        user_states[user_id] = {"target_channel": None, "ch_name": "", "files": [], "status_msg_id": None}
        save_json(STATES_FILE, user_states)
    
    keyboard = [[InlineKeyboardButton("Select Target Channel 🎯", callback_data="choose_ch")]]
    await safe_api_call(message.reply_text, "👋 Welcome! Apna target channel select karein jahan files bhejna chahte hain:", reply_markup=InlineKeyboardMarkup(keyboard))

# 5. CLEAR QUEUE COMMAND
@app.on_message(filters.command("clear") & filters.private & is_owner)
async def clear_queue_cmd(client, message):
    user_id = str(message.from_user.id)
    async with file_lock:
        user_states = load_json(STATES_FILE, {})
        if user_id in user_states:
            user_states[user_id]["files"] = []
            user_states[user_id]["status_msg_id"] = None
            save_json(STATES_FILE, user_states)
        await safe_api_call(message.reply_text, "🗑️ Aapki pending files ki queue ko poori tarah saaf kar diya gaya hai.")

# 6. CALLBACK QUERY HANDLER
@app.on_callback_query()
async def handle_buttons(client, callback_query):
    user_id = str(callback_query.from_user.id)
    
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("❌ Aapko is button par click karne ki permission nahi hai!", show_alert=True)
        return

    data = callback_query.data
    async with file_lock:
        channels_dict = load_json(CHANNELS_FILE, {})
        user_states = load_json(STATES_FILE, {})

    if data == "clear_and_start":
        async with file_lock:
            user_states[user_id] = {"target_channel": None, "ch_name": "", "files": [], "status_msg_id": None}
            save_json(STATES_FILE, user_states)
        buttons = [[InlineKeyboardButton("Select Target Channel 🎯", callback_data="choose_ch")]]
        await safe_api_call(callback_query.message.edit_text, "🗑️ Purani queue clear ho gayi. Naya channel select karein:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "choose_ch":
        if not channels_dict:
            await safe_api_call(callback_query.message.edit_text, "⚠️ Koi channel added nahi hai. Use `/addchannel` first.")
            return
        buttons = []
        channel_names = list(channels_dict.keys())
        for i in range(0, len(channel_names), 2):
            row = [InlineKeyboardButton(name, callback_data=f"select_{name}") for name in channel_names[i:i+2]]
            buttons.append(row)
        await safe_api_call(callback_query.message.edit_text, "🎯 Kis channel par data forward karna hai? Select karein:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("select_"):
        ch_name = data.replace("select_", "")
        async with file_lock:
            ch_data = channels_dict[ch_name]
            target_id = ch_data["id"] if isinstance(ch_data, dict) else int(ch_data)
            
            user_states[user_id]["target_channel"] = target_id
            user_states[user_id]["ch_name"] = ch_name
            user_states[user_id]["status_msg_id"] = None
            if "files" not in user_states[user_id]:
                user_states[user_id]["files"] = []
            save_json(STATES_FILE, user_states)
        
        await safe_api_call(
            callback_query.message.edit_text,
            f"✅ **Target Set:** `{ch_name}`\n\nAb aap bulk files bina dare forward karke yahan bhej dijiye. Saari files bhejne ke baad niche click karein:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Send All Files 🚀", callback_data="send_batch")],
                [InlineKeyboardButton("Cancel / Clear Queue ❌", callback_data="clear_and_start")]
            ])
        )

    elif data == "send_batch":
        u_state = user_states.get(user_id)
        if not u_state or "files" not in u_state or len(u_state["files"]) == 0:
            await callback_query.answer("⚠️ Queue khali hai! Pehle files forward karke bhejien.", show_alert=True)
            return

        await safe_api_call(callback_query.message.edit_reply_markup, reply_markup=None)
        
        u_state["files"].sort(key=lambda x: x["msg_id"])
        total_files = len(u_state["files"])
        
        await safe_api_call(callback_query.message.edit_text, f"🔄 Copying & Posting: 0/{total_files} completed...")
        target_chat_id = int(u_state["target_channel"])
        
        success_count = 0
        failed_files = [] 

        for index, file_data in enumerate(u_state["files"], 1):
            is_copied = await safe_api_call(
                client.copy_message,
                chat_id=target_chat_id,
                from_chat_id=int(file_data["from_chat_id"]),
                message_id=int(file_data["msg_id"])
            )
            
            if is_copied:
                success_count += 1
            else:
                failed_files.append(file_data)
                
            if index % 5 == 0 or index == total_files:
                await safe_api_call(callback_query.message.edit_text, f"🚀 Live Progress: {index}/{total_files} files processed...")
            
            await asyncio.sleep(1.2) 

        if failed_files:
            await safe_api_call(callback_query.message.edit_text, f"🔄 Retrying failed items one final time ({len(failed_files)} remaining)...")
            still_failed = []
            for file_data in failed_files:
                is_copied_final = await safe_api_call(
                    client.copy_message,
                    chat_id=target_chat_id,
                    from_chat_id=int(file_data["from_chat_id"]),
                    message_id=int(file_data["msg_id"])
                )
                if not is_copied_final:
                    still_failed.append(file_data)
                await asyncio.sleep(1.5)
            failed_files = still_failed

        final_failed_count = len(failed_files)
        await safe_api_call(
            callback_query.message.edit_text,
            f"✅ **Batch Process Finished!**\n\n"
            f"🔹 **Successfully Copied:** {success_count}\n"
            f"🔸 **Failed:** {final_failed_count}\n"
            f"✨ **Total Queue:** {total_files}\n\n"
            f"Destination Target Channel: `{u_state['ch_name']}`"
        )
        
        async with file_lock:
            user_states = load_json(STATES_FILE, {})
            user_states[user_id]["files"] = []
            user_states[user_id]["status_msg_id"] = None
            save_json(STATES_FILE, user_states)

async def finalize_batch(client, chat_id, user_id):
    try:
        await asyncio.sleep(3)
    except asyncio.CancelledError:
        return 
    
    async with file_lock:
        user_states = load_json(STATES_FILE, {})
        u_state = user_states.get(user_id)
        if not u_state: return
        
        status_msg_id = u_state.get("status_msg_id")
        total_queued = len(u_state.get("files", []))
        
        user_states[user_id]["status_msg_id"] = None
        save_json(STATES_FILE, user_states)

    if status_msg_id and total_queued > 0:
        text = f"✅ {total_queued} files successfully added to the secure queue.\nPress Send to start forwarding."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Send All Files 🚀", callback_data="send_batch")],
            [InlineKeyboardButton("Cancel / Clear Queue ❌", callback_data="clear_and_start")]
        ])
        await safe_api_call(client.edit_message_text, chat_id, status_msg_id, text, reply_markup=keyboard)


# ---------------------------------------------------------
# ✨ NAYA FEATURE: CHANNEL KI ID KE HISAB SE PHOTO + PERFORMER NAME
# ---------------------------------------------------------
@app.on_message(filters.audio & filters.private & is_owner)
async def attach_specific_channel_cover(client, message):
    user_id = str(message.from_user.id)
    
    async with file_lock:
        user_states = load_json(STATES_FILE, {})
        channels_dict = load_json(CHANNELS_FILE, {})

    u_state = user_states.get(user_id)
    
    # 1. Check karein ki user ne target channel select kiya hai ya nahi
    if not u_state or not u_state.get("ch_name"):
        await safe_api_call(message.reply_text, "❌ Kripya pehle `/start` dabakar ek Target Channel select karein jiske hisab se audio mein photo lagani hai!")
        return

    ch_name = u_state["ch_name"]
    ch_data = channels_dict.get(ch_name)
    
    if isinstance(ch_data, dict) and ch_data.get("cover_url"):
        cover_image_url = ch_data["cover_url"]
    else:
        old_id = ch_data if isinstance(ch_data, int) else ch_data.get("id")
        warning_msg = (
            f"❌ `{ch_name}` ke liye koi photo link save nahi hai!\n\n"
            f"Kripya pehle is command ko copy karke bhejien:\n"
            f"`/addchannel {ch_name} {old_id} https://Aapki_Photo_Ka_Link.jpg`"
        )
        await safe_api_call(message.reply_text, warning_msg)
        return

    status_msg = await safe_api_call(message.reply_text, f"🎧 Audio mili!\n🎯 Target: `{ch_name}`\n⏳ Photo aur Performer Name set kar raha hoon...", quote=True)
    if not status_msg: return

    temp_cover_path = f"temp_cover_{message.id}.jpg"

    try:
        # 1. Link se photo download karna
        try:
            urllib.request.urlretrieve(cover_image_url, temp_cover_path)
        except Exception as e:
            await safe_api_call(status_msg.edit_text, f"❌ Link se photo download nahi ho payi. Link check karein!\nError: {e}")
            return

        # 2. Audio file download karna
        file_path = await message.download()
        
        # 3. Mutagen se audio open karna
        audio = MP3(file_path, ID3=ID3)
        
        # Original Title (Name) nikal kar save karna taaki wo delete na ho jaye
        original_title = None
        if audio.tags and 'TIT2' in audio.tags:
            original_title = audio.tags['TIT2'].text[0]
        
        try:
            audio.add_tags()
        except error:
            pass

        # Purani cover photos aur artist name hata dena
        audio.tags.delall("APIC")
        audio.tags.delall("TPE1")

        # Naya Performer Name Set Karna (Screenshot ke hisab se)
        audio.tags.add(TPE1(encoding=3, text=PERFORMER_NAME))

        # Apni custom photo attach karna
        with open(temp_cover_path, "rb") as albumart:
            audio.tags.add(
                APIC(
                    encoding=3, 
                    mime='image/jpeg', 
                    type=3, 
                    desc=u'Cover',
                    data=albumart.read()
                )
            )
        
        audio.save()

        # 4. Modified audio wapas Telegram par bhejna
        await safe_api_call(status_msg.edit_text, "✅ Photo aur Performer Name set ho gaya! Wapas bhej raha hoon...")
        
        await safe_api_call(
            client.send_audio,
            chat_id=message.chat.id,
            audio=file_path,
            thumb=temp_cover_path,
            title=original_title,         # Original naam waisa hi rahega
            performer=PERFORMER_NAME,     # ✨ Title ke niche naya naam dikhega
            caption=message.caption or "",
            reply_to_message_id=message.id
        )

        await safe_api_call(status_msg.delete)
            
    except Exception as e:
        logging.error(f"Error attaching tags: {e}")
        await safe_api_call(status_msg.edit_text, f"❌ Data set karne mein error aayi: {str(e)}")
    
    finally:
        if os.path.exists(temp_cover_path):
            os.remove(temp_cover_path)
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)


# ---------------------------------------------------------
# 7. UNIVERSAL MESSAGE HANDLER (Optimized Batch UI)
# ---------------------------------------------------------
@app.on_message(filters.private & ~filters.command(["start", "addchannel", "removechannel", "listchannels", "clear"]) & ~filters.audio)
async def collect_batch(client, message):
    if message.from_user.id != OWNER_ID:
        return

    user_id = str(message.from_user.id)
    chat_id = message.chat.id
    
    async with file_lock:
        user_states = load_json(STATES_FILE, {})

    if user_id not in user_states or not user_states[user_id].get("target_channel"):
        await safe_api_call(message.reply_text, "⚠️ Kripya pehle `/start` dabakar target channel select karein!")
        return

    if "files" not in user_states[user_id]:
        user_states[user_id]["files"] = []

    if any(f["msg_id"] == message.id for f in user_states[user_id]["files"]):
        return

        async with file_lock:
        user_states[user_id]["files"].append({
            "msg_id": message.id,
            "from_chat_id": message.chat.id
        })
        total_queued = len(user_states[user_id]["files"])
        status_msg_id = user_states[user_id].get("status_msg_id")
        save_json(STATES_FILE, user_states)
    
    is_creating = pending_status_creation.get(user_id, False)

    if not status_msg_id and not is_creating:
        pending_status_creation[user_id] = True
        msg = await safe_api_call(message.reply_text, f"📥 Queueing files... ({total_queued})", quote=True)
        if msg:
            async with file_lock:
                user_states = load_json(STATES_FILE, {})
                user_states[user_id]["status_msg_id"] = msg.id
                save_json(STATES_FILE, user_states)
        pending_status_creation[user_id] = False

    elif status_msg_id:
        now = time.time()
        if now - last_edit_time.get(user_id, 0) > 1.5:
            last_edit_time[user_id] = now
            await safe_api_call(client.edit_message_text, chat_id, status_msg_id, f"📥 Queueing files... ({total_queued})")

    if user_id in active_timers:
        active_timers[user_id].cancel()
    
    active_timers[user_id] = asyncio.create_task(finalize_batch(client, chat_id, user_id))


if __name__ == "__main__":
    print("Bot started successfully! Custom Photo + Performer feature active!")
    app.run()
