import os
import sys
import re
import json
import hashlib
import hmac as hmac_mod
import struct
import sqlite3
import subprocess
import tempfile
import ctypes
import ctypes.wintypes
import shutil
import time
from pathlib import Path
from Cryptodome.Cipher import AES
from typing import Optional, List, Dict, Tuple

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16

kernel32 = ctypes.windll.kernel32


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", ctypes.wintypes.DWORD), ("_pad1", ctypes.wintypes.DWORD),
        ("RegionSize", ctypes.c_uint64), ("State", ctypes.wintypes.DWORD),
        ("Protect", ctypes.wintypes.DWORD), ("Type", ctypes.wintypes.DWORD), ("_pad2", ctypes.wintypes.DWORD),
    ]

MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}


def read_mem(h, addr, sz):
    buf = ctypes.create_string_buffer(sz)
    n = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n)):
        return buf.raw[:n.value]
    return None


def enum_regions(h):
    regs = []
    addr = 0
    mbi = MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < mbi.RegionSize < 500 * 1024 * 1024:
            regs.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return regs


def _get_pids(process_name="Weixin.exe"):
    """Get all PIDs for the given process name."""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, creationflags=0x08000000,
        )
    except Exception:
        return []
    pids = []
    for line in r.stdout.strip().split('\n'):
        if not line.strip():
            continue
        p = line.strip('"').split('","')
        if len(p) >= 5:
            try:
                pid = int(p[1])
                mem_str = p[4].replace(',', '').replace(' K', '').strip()
                mem = int(mem_str) if mem_str.isdigit() else 0
                pids.append((pid, mem))
            except (ValueError, IndexError):
                continue
    pids.sort(key=lambda x: x[1], reverse=True)
    return pids


def verify_enc_key(enc_key, db_page1):
    """Verify enc_key by HMAC check on page 1. Tries both raw key and PBKDF2 passphrase."""
    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    
    # Approach 1: raw key + HMAC-SHA512 (WeChat 4.0.x new WCDB)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    if hm.digest() == stored_hmac:
        return True
    
    # Approach 2: PBKDF2 passphrase derivation (WeChat 4.1+)
    for kdf_hash in ['sha512', 'sha1']:
        for kdf_it in [256000, 102400, 64000]:
            try:
                derived_key = hashlib.pbkdf2_hmac(kdf_hash, enc_key, salt, kdf_it, KEY_SZ)
                mac_key2 = hashlib.pbkdf2_hmac("sha512", derived_key, mac_salt, 2, dklen=KEY_SZ)
                hm2 = hmac_mod.new(mac_key2, hmac_data, hashlib.sha512)
                hm2.update(struct.pack("<I", 1))
                if hm2.digest() == stored_hmac:
                    return True
            except:
                continue
    
    return False


def collect_db_files(db_dir):
    """Collect all .db files and their salts."""
    db_files = []
    salt_to_dbs = {}
    for root, dirs, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db") or name.endswith("-wal") or name.endswith("-shm"):
                continue
            path = os.path.join(root, name)
            size = os.path.getsize(path)
            if size < PAGE_SZ:
                continue
            try:
                with open(path, "rb") as f:
                    page1 = f.read(PAGE_SZ)
            except Exception:
                continue
            rel = os.path.relpath(path, db_dir)
            salt = page1[:SALT_SZ].hex()
            db_files.append((rel, path, size, salt, page1))
            salt_to_dbs.setdefault(salt, []).append(rel)
    return db_files, salt_to_dbs


def scan_memory_for_keys(data, hex_re, db_files, salt_to_dbs, key_map,
                         remaining_salts, base_addr, pid):
    """Scan memory data for hex key patterns and verify."""
    matches = 0
    for m in hex_re.finditer(data):
        hex_str = m.group(1).decode()
        addr = base_addr + m.start()
        matches += 1
        hex_len = len(hex_str)

        if hex_len == 96:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[64:]
            if salt_hex in remaining_salts:
                enc_key = bytes.fromhex(enc_key_hex)
                for rel, path, sz, s, page1 in db_files:
                    if s == salt_hex and verify_enc_key(enc_key, page1):
                        key_map[salt_hex] = enc_key_hex
                        remaining_salts.discard(salt_hex)
                        break

        elif hex_len == 64:
            if not remaining_salts:
                continue
            enc_key_hex = hex_str
            enc_key = bytes.fromhex(enc_key_hex)
            for rel, path, sz, salt_hex_db, page1 in db_files:
                if salt_hex_db in remaining_salts and verify_enc_key(enc_key, page1):
                    key_map[salt_hex_db] = enc_key_hex
                    remaining_salts.discard(salt_hex_db)
                    break

        elif hex_len > 96 and hex_len % 2 == 0:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[-32:]
            if salt_hex in remaining_salts:
                enc_key = bytes.fromhex(enc_key_hex)
                for rel, path, sz, s, page1 in db_files:
                    if s == salt_hex and verify_enc_key(enc_key, page1):
                        key_map[salt_hex] = enc_key_hex
                        remaining_salts.discard(salt_hex)
                        break

    return matches


def extract_keys_memory_scanning(db_dir):
    """Try to extract keys via memory scanning (works for WeChat 4.0.x and some 4.1.x)."""
    db_files, salt_to_dbs = collect_db_files(db_dir)
    if not db_files:
        return {}, []

    pids = _get_pids("Weixin.exe") or _get_pids("WeChat.exe")
    if not pids:
        return {}, db_files

    hex_re = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
    key_map = {}
    remaining_salts = set(salt_to_dbs.keys())

    for pid, mem_kb in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            continue
        try:
            regions = enum_regions(h)
            for base, size in regions:
                data = read_mem(h, base, size)
                if not data:
                    continue
                scan_memory_for_keys(
                    data, hex_re, db_files, salt_to_dbs,
                    key_map, remaining_salts, base, pid,
                )
                if not remaining_salts:
                    break
        finally:
            kernel32.CloseHandle(h)
        if not remaining_salts:
            break

    if not key_map:
        key_map = _scan_with_pymem(db_files, salt_to_dbs, remaining_salts)

    return key_map, db_files


def _scan_with_pymem(db_files, salt_to_dbs, remaining_salts):
    """Use pymem for more thorough memory scanning (for WeChat 4.1.10+)."""
    key_map = {}
    try:
        import pymem
        import pymem.process
    except ImportError:
        return key_map

    pids = _get_pids("Weixin.exe") or _get_pids("WeChat.exe")
    if not pids:
        return key_map

    hex_re = re.compile(rb"x'([0-9a-fA-F]{64,192})'")

    for pid, mem_kb in pids:
        try:
            pm = pymem.Pymem()
            pm.open_process_from_id(pid)
        except Exception:
            continue

        try:
            addresses = pm.pattern_scan_all(b"x'", return_multiple=True)
            for a in addresses:
                try:
                    b = pm.read_bytes(a, 200)
                    for m in hex_re.finditer(b):
                        hex_str = m.group(1).decode()
                        hex_len = len(hex_str)

                        if hex_len == 96:
                            enc_key_hex = hex_str[:64]
                            salt_hex = hex_str[64:]
                            if salt_hex in remaining_salts:
                                enc_key = bytes.fromhex(enc_key_hex)
                                for rel, path, sz, s, page1 in db_files:
                                    if s == salt_hex and verify_enc_key(enc_key, page1):
                                        key_map[salt_hex] = enc_key_hex
                                        remaining_salts.discard(salt_hex)
                                        break

                        elif hex_len == 64:
                            if not remaining_salts:
                                continue
                            enc_key_hex = hex_str
                            enc_key = bytes.fromhex(enc_key_hex)
                            for rel, path, sz, salt_hex_db, page1 in db_files:
                                if salt_hex_db in remaining_salts and verify_enc_key(enc_key, page1):
                                    key_map[salt_hex_db] = enc_key_hex
                                    remaining_salts.discard(salt_hex_db)
                                    break

                        if not remaining_salts:
                            break
                except Exception:
                    continue

                if not remaining_salts:
                    break

            if not remaining_salts:
                break

            if not key_map:
                _scan_binary_keys_pymem(pm, db_files, salt_to_dbs, remaining_salts, key_map)

        except Exception:
            continue
        finally:
            try:
                pm.close_process()
            except Exception:
                pass

        if not remaining_salts:
            break

    return key_map


def _scan_binary_keys_pymem(pm, db_files, salt_to_dbs, remaining_salts, key_map):
    """Scan for raw 32-byte binary keys using pymem's pattern scan."""
    try:
        all_regions = []
        for region in pm.list_modules():
            try:
                base = region.lpBaseOfDll
                size = region.RegionSize
                if size > 0:
                    all_regions.append((base, size))
            except Exception:
                continue

        for base, size in all_regions:
            if not remaining_salts:
                break
            try:
                data = pm.read_bytes(base, min(size, 10 * 1024 * 1024))
                for offset in range(0, len(data) - 32, 4):
                    candidate = data[offset:offset + 32]
                    if candidate == b'\x00' * 32:
                        continue
                    if len(set(candidate)) < 8:
                        continue
                    for rel, path, sz, salt_hex, page1 in db_files:
                        if salt_hex in remaining_salts and verify_enc_key(candidate, page1):
                            key_map[salt_hex] = candidate.hex()
                            remaining_salts.discard(salt_hex)
                            break
            except Exception:
                continue
    except Exception:
        pass


def find_wx_key_dll():
    """Find wx_key.dll in known locations."""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    candidates = [
        os.path.join(base_path, "wx_key", "wx_key.dll"),
        os.path.join(base_path, "wx_key.dll"),
    ]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    candidates.extend([
        os.path.join(project_root, "wx_key", "data", "flutter_assets", "assets", "dll", "wx_key.dll"),
        os.path.join(project_root, "wx_key", "wx_key.dll"),
        r"D:\wx_key\data\flutter_assets\assets\dll\wx_key.dll",
    ])

    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def extract_keys_dll_hook(db_dir, progress_callback=None):
    """Extract keys using wx_key.dll DLL hooking (works for WeChat 4.1+)."""
    dll_path = find_wx_key_dll()
    if not dll_path:
        raise RuntimeError(
            "未找到 wx_key.dll\n"
            "请将 wx_key 工具放置在程序目录下的 wx_key 文件夹中"
        )

    pids = _get_pids("Weixin.exe") or _get_pids("WeChat.exe")
    if not pids:
        raise RuntimeError("未找到微信进程，请确保微信已登录")

    main_pid = pids[0][0]
    if progress_callback:
        progress_callback(0, 0, f"正在Hook微信进程 PID={main_pid}...", True)

    dll = None
    try:
        dll = ctypes.CDLL(dll_path)
    except OSError as e:
        raise RuntimeError(f"无法加载 wx_key.dll: {e}\n路径: {dll_path}")

    try:
        dll.InitializeHook.argtypes = [ctypes.wintypes.DWORD]
        dll.InitializeHook.restype = ctypes.c_bool

        dll.PollKeyData.argtypes = [ctypes.c_char_p, ctypes.c_int]
        dll.PollKeyData.restype = ctypes.c_bool

        dll.GetStatusMessage.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        dll.GetStatusMessage.restype = ctypes.c_bool

        dll.CleanupHook.argtypes = []
        dll.CleanupHook.restype = None

        dll.GetLastErrorMsg.argtypes = []
        dll.GetLastErrorMsg.restype = ctypes.c_char_p
    except Exception as e:
        raise RuntimeError(f"设置DLL函数类型失败: {e}")

    hook_initialized = False
    try:
        if not dll.InitializeHook(main_pid):
            err_msg = "未知错误"
            try:
                err = dll.GetLastErrorMsg()
                if err:
                    err_msg = err.decode('utf-8', errors='replace')
            except Exception:
                pass
            raise RuntimeError(f"DLL初始化失败: {err_msg}")

        hook_initialized = True
        if progress_callback:
            progress_callback(1, 10, "Hook成功，等待微信打开数据库以捕获密钥...", True)

        key_buf = ctypes.create_string_buffer(128)
        log_buf = ctypes.create_string_buffer(512)
        level = ctypes.c_int(0)

        found_key = None
        max_wait = 60
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                if dll.PollKeyData(key_buf, 128):
                    key_hex = key_buf.value.decode('ascii', errors='ignore')
                    if key_hex and len(key_hex) >= 64:
                        found_key = key_hex[:64]
                        if progress_callback:
                            progress_callback(5, 10, f"捕获到密钥: {found_key[:16]}...", True)
                        break
            except Exception:
                pass

            try:
                while dll.GetStatusMessage(log_buf, 512, ctypes.byref(level)):
                    msg = log_buf.value.decode('utf-8', errors='replace')
                    if progress_callback:
                        progress_callback(2, 10, f"DLL: {msg}", True)
            except Exception:
                pass

            time.sleep(0.5)

        if not found_key:
            raise RuntimeError(
                "等待超时，未能捕获到密钥。\n"
                "请尝试：\n"
                "1. 在微信中打开一个聊天\n"
                "2. 或重新登录微信"
            )

        key_map = {}
        db_files, salt_to_dbs = collect_db_files(db_dir)

        enc_key = bytes.fromhex(found_key)
        for rel, path, sz, salt_hex, page1 in db_files:
            if verify_enc_key(enc_key, page1):
                key_map[salt_hex] = found_key

        return key_map, db_files

    finally:
        if hook_initialized and dll:
            try:
                dll.CleanupHook()
            except Exception:
                pass


def extract_keys_from_weixin(db_dir, progress_callback=None):
    """Main key extraction entry point. Tries multiple methods."""
    if progress_callback:
        progress_callback(0, 0, "尝试方法1: 内存扫描...", True)

    key_map, db_files = extract_keys_memory_scanning(db_dir)

    if key_map:
        if progress_callback:
            progress_callback(1, 1, f"内存扫描成功，提取到 {len(key_map)} 个密钥", True)
        return key_map, db_files

    if progress_callback:
        progress_callback(0, 0, "内存扫描未找到密钥，尝试方法2: DLL Hook...", True)

    try:
        key_map, db_files = extract_keys_dll_hook(db_dir, progress_callback)
        if key_map:
            if progress_callback:
                progress_callback(1, 1, f"DLL Hook成功，提取到 {len(key_map)} 个密钥", True)
            return key_map, db_files
    except Exception as e:
        if progress_callback:
            progress_callback(0, 0, f"DLL Hook失败: {e}", False)

    raise RuntimeError(
        "所有密钥提取方法均失败。\n"
        "请确保：\n"
        "1. 微信已登录并正在运行\n"
        "2. 本程序以管理员权限运行\n"
        "3. wx_key.dll 文件存在于正确位置\n"
        "4. 在微信中打开一个聊天窗口"
    )


def decrypt_db(db_path, enc_key_hex, output_path):
    """Decrypt WeChat database by trying all known algorithms.
    
    Tries:
    1. Raw key + HMAC-SHA512 + reserve=80 (WeChat 4.0.x with new WCDB)
    2. pywxdump: SHA1 PBKDF2 64K + HMAC-SHA1 + reserve=48 (WeChat 4.0.x old)
    3. PBKDF2-SHA512 passphrase derivation 256K (WeChat 4.1+)
    4. Raw key + HMAC-SHA1 + reserve=48 (WeChat 3.x)
    """
    if not os.path.exists(db_path) or not os.path.isfile(db_path):
        return False
    if len(enc_key_hex) != 64:
        return False

    try:
        rawkey = bytes.fromhex(enc_key_hex.strip())
        with open(db_path, "rb") as f:
            blist = f.read()

        if len(blist) < 4096:
            return False

        SQLITE_FILE_HEADER = b"SQLite format 3\x00"
        KEY_SZ = 32
        PAGE_SZ = 4096
        SALT_SZ = 16
        IV_SZ = 16
        HMAC_SHA512_SZ = 64
        RESERVE_80 = 80
        RESERVE_48 = 48

        salt = blist[:SALT_SZ]
        page1 = blist[SALT_SZ:PAGE_SZ]
        mac_salt = bytes(x ^ 0x3a for x in salt)

        algorithms = [
            ("raw_sha512_res80", lambda k, s, ms, p: (
                hashlib.pbkdf2_hmac('sha512', k, ms, 2, KEY_SZ),
                'sha512', p[:-64], p[-64:]
            )),
            ("pywxdump", lambda k, s, ms, p: (
                hashlib.pbkdf2_hmac('sha1',
                    hashlib.pbkdf2_hmac('sha1', k, s, 64000, KEY_SZ),
                    ms, 2, KEY_SZ),
                'sha1', blist[16:4064], blist[16:4096][-32:-12]
            )),
            ("raw_sha512_res48", lambda k, s, ms, p: (
                hashlib.pbkdf2_hmac('sha512', k, ms, 2, KEY_SZ),
                'sha512', p[:-48+16], p[-48+16:][:64]
            )),
            ("raw_sha1_res80", lambda k, s, ms, p: (
                hashlib.pbkdf2_hmac('sha1', k, ms, 2, KEY_SZ),
                'sha1', p[:-80+16], p[-80+16:][:20]
            )),
            ("raw_sha1_res48", lambda k, s, ms, p: (
                hashlib.pbkdf2_hmac('sha1', k, ms, 2, KEY_SZ),
                'sha1', p[:4080-48+16], p[4080-48+16:4080-48+16+20]
            )),
        ]

        for name, make_params in algorithms:
            try:
                if name == "pywxdump":
                    mac_key, hmac_algo, data, stored = make_params(rawkey, salt, mac_salt, page1)
                else:
                    mac_key, hmac_algo, data, stored = make_params(rawkey, salt, mac_salt, page1)
                
                hm = hmac_mod.new(mac_key, digestmod=hmac_algo)
                if name == "pywxdump":
                    hm.update(data)
                else:
                    hm.update(data)
                hm.update(b'\x01\x00\x00\x00')
                
                if hm.digest() != stored:
                    continue

                # HMAC matched - now decrypt
                os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
                
                if name == "pywxdump":
                    # pywxdump uses derived key for AES
                    enc_key = hashlib.pbkdf2_hmac("sha1", rawkey, salt, 64000, KEY_SZ)
                    with open(output_path, "wb") as out:
                        out.write(SQLITE_FILE_HEADER)
                        for i in range(0, len(blist), PAGE_SZ):
                            tblist = blist[i:i+PAGE_SZ] if i > 0 else blist[16:i+PAGE_SZ]
                            out.write(AES.new(enc_key, AES.MODE_CBC, tblist[-RESERVE_48:-32]).decrypt(tblist[:-RESERVE_48]))
                            out.write(tblist[-RESERVE_48:])
                else:
                    with open(output_path, "wb") as out:
                        out.write(SQLITE_FILE_HEADER)
                        pages = [page1]
                        pages += [blist[i:i+PAGE_SZ] for i in range(PAGE_SZ, len(blist), PAGE_SZ)]
                        res = RESERVE_80 if 'res80' in name else RESERVE_48
                        for p in pages:
                            iv = p[-res:-res+IV_SZ]
                            t = AES.new(rawkey, AES.MODE_CBC, iv)
                            out.write(t.decrypt(p[:-res]))
                            out.write(p[-res:])

                # Verify decrypted file
                try:
                    import sqlite3 as _sqlite3
                    conn = _sqlite3.connect(output_path)
                    conn.execute("SELECT count(*) FROM sqlite_master")
                    conn.close()
                    return True
                except Exception:
                    try:
                        os.remove(output_path)
                    except:
                        pass
                    continue

            except Exception:
                continue

        # Approach 6: PBKDF2 passphrase derivation for WeChat 4.1+
        try:
            for kdf_it in [256000, 102400, 64000]:
                for kdf_hash in ['sha512', 'sha1']:
                    enc_key = hashlib.pbkdf2_hmac(kdf_hash, rawkey, salt, kdf_it, KEY_SZ)
                    mac_key = hashlib.pbkdf2_hmac('sha512', enc_key, mac_salt, 2, KEY_SZ)
                    hm = hmac_mod.new(mac_key, digestmod='sha512')
                    hm.update(page1[:-64])
                    hm.update(b'\x01\x00\x00\x00')
                    if hm.digest() == page1[-64:]:
                        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
                        with open(output_path, "wb") as out:
                            out.write(SQLITE_FILE_HEADER)
                            pages = [page1]
                            pages += [blist[i:i+PAGE_SZ] for i in range(PAGE_SZ, len(blist), PAGE_SZ)]
                            for p in pages:
                                iv = p[-RESERVE_80:-RESERVE_80+IV_SZ]
                                t = AES.new(enc_key, AES.MODE_CBC, iv)
                                out.write(t.decrypt(p[:-RESERVE_80]))
                                out.write(p[-RESERVE_80:])
                        try:
                            import sqlite3 as _sqlite3
                            conn = _sqlite3.connect(output_path)
                            conn.execute("SELECT count(*) FROM sqlite_master")
                            conn.close()
                            return True
                        except:
                            try:
                                os.remove(output_path)
                            except:
                                pass
        except:
            pass

        return False
    except Exception:
        try:
            os.remove(output_path)
        except Exception:
            pass
        return False


def find_wechat_data_dirs():
    """Find all WeChat data directories."""
    candidates = []
    user_profile = os.environ.get("USERPROFILE", "")
    doc_dir = os.path.join(user_profile, "Documents")
    appdata = os.environ.get("APPDATA", "")

    search_bases = [
        os.path.join(doc_dir, "WeChat Files"),
        os.path.join(appdata, "Tencent", "xwechat_files"),
    ]

    for base in search_bases:
        if not os.path.exists(base):
            continue
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if os.path.isdir(full):
                has_msg = os.path.isdir(os.path.join(full, "Msg"))
                has_db = os.path.isdir(os.path.join(full, "db")) or os.path.isdir(os.path.join(full, "db_storage"))
                if has_msg or has_db:
                    candidates.append(full)

    if not candidates:
        for base in search_bases:
            if os.path.exists(base):
                for entry in os.listdir(base):
                    full = os.path.join(base, entry)
                    if os.path.isdir(full):
                        candidates.append(full)
    return candidates


class WeChatDatabase:
    """Manages WeChat database operations."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.msg_dir = self._find_msg_dir()
        self.temp_dir = None
        self._decrypted = False
        self.key_map = {}
        self.db_files_info = []

    def _find_msg_dir(self):
        """Find the Msg directory containing .db files."""
        msg_dir = os.path.join(self.data_dir, "Msg")
        if os.path.isdir(msg_dir):
            db_files = [f for f in os.listdir(msg_dir) if f.endswith('.db')]
            if db_files:
                return msg_dir

        multi_dir = os.path.join(self.data_dir, "Msg", "Multi")
        if os.path.isdir(multi_dir):
            db_files = [f for f in os.listdir(multi_dir) if f.endswith('.db')]
            if db_files:
                return multi_dir

        candidates = [
            os.path.join(self.data_dir, "db"),
            os.path.join(self.data_dir, "db_storage"),
            self.data_dir,
        ]
        for c in candidates:
            if os.path.isdir(c) and any(f.endswith('.db') for f in os.listdir(c)):
                return c
        return self.data_dir

    def extract_keys(self, progress_callback=None):
        """Extract database keys from Weixin.exe memory."""
        try:
            self.key_map, self.db_files_info = extract_keys_from_weixin(
                self.msg_dir, progress_callback
            )
        except Exception as e:
            if progress_callback:
                progress_callback(0, 0, f"密钥提取失败: {e}", False)
            raise
        return bool(self.key_map)

    def decrypt_all(self, progress_callback=None):
        """Decrypt all databases using extracted keys."""
        if not self.key_map:
            raise RuntimeError("请先提取数据库密钥")

        self.temp_dir = tempfile.mkdtemp(prefix="wechat_decrypted_")

        all_db_files = []
        search_dir = os.path.dirname(self.msg_dir)
        for root, dirs, files in os.walk(search_dir):
            for name in files:
                if name.endswith('.db') and not name.endswith('-wal') and not name.endswith('-shm'):
                    all_db_files.append(os.path.join(root, name))

        total = len(all_db_files)
        success_count = 0

        for i, db_path in enumerate(all_db_files):
            db_name = os.path.relpath(db_path, search_dir)
            dst = os.path.join(self.temp_dir, db_name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)

            success = False
            try:
                with open(db_path, "rb") as f:
                    page1 = f.read(PAGE_SZ)
                if len(page1) >= SALT_SZ:
                    salt = page1[:SALT_SZ].hex()
                    if salt in self.key_map:
                        enc_key_hex = self.key_map[salt]
                        success = decrypt_db(db_path, enc_key_hex, dst)
            except Exception:
                success = False

            if success:
                success_count += 1

            if progress_callback:
                try:
                    progress_callback(i + 1, total, os.path.basename(db_path), success)
                except Exception:
                    pass

        self._decrypted = True
        return success_count > 0

    def _get_db_path(self, db_name):
        """Get the path to a database (decrypted or original)."""
        if self._decrypted and self.temp_dir:
            for root, dirs, files in os.walk(self.temp_dir):
                for f in files:
                    if f == db_name:
                        return os.path.join(root, f)

        for root, dirs, files in os.walk(self.data_dir):
            for f in files:
                if f == db_name:
                    return os.path.join(root, f)
        return None

    def _safe_connect(self, db_path):
        """Open database safely."""
        if not db_path:
            return None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM sqlite_master")
            return conn
        except Exception:
            return None

    def get_contacts(self):
        """Get all contacts."""
        contacts = []
        db_path = self._get_db_path("MicroMsg.db")
        if not db_path:
            return contacts

        conn = self._safe_connect(db_path)
        if not conn:
            return contacts

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            contact_table = None
            for t in tables:
                try:
                    cursor.execute(f"PRAGMA table_info({t})")
                    cols = [row[1] for row in cursor.fetchall()]
                    col_lower = [c.lower() for c in cols]
                    if 'username' in col_lower and 'nickname' in col_lower:
                        contact_table = t
                        break
                except Exception:
                    continue

            if not contact_table:
                return contacts

            cursor.execute(f"PRAGMA table_info({contact_table})")
            cols = [row[1] for row in cursor.fetchall()]
            col_map = {c.lower(): c for c in cols}

            user_col = col_map.get('username', '')
            nick_col = col_map.get('nickname', '')
            remark_col = col_map.get('conremark', col_map.get('remark', ''))

            if not user_col or not nick_col:
                return contacts

            query = f"SELECT {user_col}, {nick_col}"
            if remark_col:
                query += f", {remark_col}"
            query += f" FROM {contact_table}"

            cursor.execute(query)
            for row in cursor.fetchall():
                username = row[0] or ""
                nickname = row[1] or ""
                remark = row[2] if remark_col and len(row) > 2 else ""

                display_name = remark or nickname or username
                if username.startswith("gh_"):
                    continue

                contacts.append({
                    "username": username,
                    "nickname": nickname,
                    "conremark": remark,
                    "display_name": display_name,
                    "type": 3,
                })

            conn.close()
        except Exception:
            pass

        return contacts

    def get_messages(self, username, limit=10000):
        """Get messages for a specific contact."""
        messages = []

        msg_dbs = ["ChatMsg.db"]
        for root, dirs, files in os.walk(self.data_dir):
            for f in files:
                if f.endswith('.db') and 'MSG' in f.upper() and f not in msg_dbs:
                    msg_dbs.append(f)

        for db_name in msg_dbs:
            db_path = self._get_db_path(db_name)
            if not db_path:
                continue

            conn = self._safe_connect(db_path)
            if not conn:
                continue

            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

                for table in tables:
                    try:
                        cursor.execute(f"PRAGMA table_info({table})")
                        cols = [row[1] for row in cursor.fetchall()]
                        col_lower = {c.lower(): c for c in cols}

                        talker_col = col_lower.get('strtalker', col_lower.get('talker', ''))
                        content_col = col_lower.get('strcontent', col_lower.get('content', ''))
                        time_col = col_lower.get('createtime', col_lower.get('time', ''))
                        type_col = col_lower.get('type', '')
                        subtype_col = col_lower.get('subtype', '')
                        sender_col = col_lower.get('issender', col_lower.get('sender', ''))

                        if not talker_col or not content_col:
                            continue

                        query = f"SELECT {talker_col}, {content_col}"
                        if time_col:
                            query += f", {time_col}"
                        if type_col:
                            query += f", {type_col}"
                        if subtype_col:
                            query += f", {subtype_col}"
                        if sender_col:
                            query += f", {sender_col}"
                        query += f" FROM {table} WHERE {talker_col} = ?"
                        if time_col:
                            query += f" ORDER BY {time_col} ASC"
                        query += f" LIMIT {limit}"

                        cursor.execute(query, (username,))
                        for row in cursor.fetchall():
                            msg = {
                                "talker": row[0],
                                "content": row[1] or "",
                                "time": row[2] if time_col and len(row) > 2 else 0,
                                "type": row[3] if type_col and len(row) > 3 else 0,
                                "sub_type": row[4] if subtype_col and len(row) > 4 else 0,
                                "is_sender": row[5] if sender_col and len(row) > 5 else 0,
                            }
                            messages.append(msg)

                        if messages:
                            break
                    except Exception:
                        continue

                conn.close()
                if messages:
                    break
            except Exception:
                continue

        messages.sort(key=lambda x: x.get("time", 0))
        return messages

    def cleanup(self):
        """Clean up temporary files."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass
