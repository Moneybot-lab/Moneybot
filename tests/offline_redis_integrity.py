"""Execute production Lua in liblua against local, deterministic Redis commands.

No Redis server, socket, provider client or optional downloaded package is used.
"""
import ctypes
import ctypes.util
import threading


def confirmed_frame(state, messages, now):
    """A consumer fixture obtains confirmation through the actual worker path."""
    import asyncio
    import json
    from types import SimpleNamespace
    from moneybot.services.market_stream import MassiveWebSocketWorker, WorkerConfig
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline fixture cannot contact a provider')
    async def run():
        worker = MassiveWebSocketWorker(api_key='offline', state=state,
            rest_client=SimpleNamespace(get_quote=forbidden), connect_factory=forbidden,
            config=WorkerConfig(enabled=True, shadow_mode=False), clock=lambda:now)
        worker._connection_state = 'connected'
        try:
            await worker.process_raw_message(json.dumps(messages))
            await worker._publish_health(force=True)
        finally:
            await worker.aclose()
    asyncio.run(run())


class RedisLuaAdapter:
    def __init__(self, backend, *, namespace='moneybot:market:v1', initialize=True):
        self.backend = backend
        if not hasattr(backend, '_integrity_hashes'):
            backend._integrity_hashes = {}
            backend._integrity_zsets = {}
            backend._integrity_lock = threading.RLock()
        self.hashes = backend._integrity_hashes
        self.zsets = backend._integrity_zsets
        self.lock = backend._integrity_lock
        if not hasattr(backend, '_integrity_strings'):
            backend._integrity_strings = {}
        self.strings = backend._integrity_strings
        # Fixture-only initialization: this backend is explicitly new and has
        # no prior price history. Production workers never bootstrap silently.
        if initialize:
            control = 'ready|2|offline-fixture|explicitly-empty-synthetic-history'
            self.hashes.setdefault(namespace + ':integrity', {'__schema__': control, '__retired__': '0', '__ordinal__': '0'})
            self.strings.setdefault(namespace + ':integrity-control', control)

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def get(self, key):
        return self.strings[key] if key in self.strings else self.backend.get(key)

    def set(self, key, value, ex=None):
        self.hashes.pop(key, None); self.zsets.pop(key, None)
        if ex is None:
            self.strings[key] = value
            return True
        return self.backend.set(key, value, ex=ex)

    def exists(self, key):
        return bool(key in self.strings or self.hashes.get(key) or self.zsets.get(key) or self.get(key) is not None)

    def pttl(self, key):
        if key in self.strings or self.hashes.get(key) or self.zsets.get(key):
            return -1
        return -2 if self.get(key) is None else 1

    def _command(self, command, *args):
        command = command.upper()
        key = str(args[0])
        actual = ('hash' if self.hashes.get(key) else 'zset' if self.zsets.get(key)
                  else 'string' if key in self.strings or self.backend.get(key) is not None else None)
        expected = ('hash' if command in {'HGET','HLEN','HSET','HDEL','HINCRBY','HKEYS','HGETALL'}
                    else 'zset' if command in {'ZCARD','ZRANGE','ZSCORE','ZADD','ZREM'}
                    else 'string' if command == 'GET' else None)
        if actual is not None and expected is not None and actual != expected:
            raise RuntimeError('WRONGTYPE Operation against a key holding the wrong kind of value')
        if command == 'GET':
            return self.get(key)
        if command == 'PTTL':
            return self.pttl(key)
        if command == 'HGET':
            return self.hget(key, str(args[1]))
        if command == 'HLEN':
            return len(self.hashes.get(key, {}))
        if command == 'HKEYS':
            return list(self.hashes.get(key, {}))
        if command == 'HGETALL':
            return [value for pair in self.hashes.get(key, {}).items() for value in pair]
        if command == 'HSET':
            target = self.hashes.setdefault(key, {})
            for i in range(1, len(args), 2):
                target[str(args[i])] = str(args[i+1])
            return 1
        if command == 'HDEL':
            return int(self.hashes.get(key, {}).pop(str(args[1]), None) is not None)
        if command == 'HINCRBY':
            value = int(self.hget(key, str(args[1])) or 0) + int(args[2])
            self.hashes.setdefault(key, {})[str(args[1])] = str(value)
            return value
        if command == 'ZCARD':
            return len(self.zsets.get(key, {}))
        if command == 'ZRANGE':
            ordered = sorted(self.zsets.get(key, {}), key=lambda x: (self.zsets[key][x], x))
            end = None if int(args[2]) == -1 else int(args[2]) + 1
            ordered = ordered[int(args[1]):end]
            if len(args) > 3 and args[3] == 'WITHSCORES':
                return [value for field in ordered for value in (field, format(self.zsets[key][field], '.17g'))]
            return ordered
        if command == 'ZSCORE':
            return self.zsets.get(key, {}).get(str(args[1]))
        if command == 'ZREM':
            return int(self.zsets.get(key, {}).pop(str(args[1]), None) is not None)
        if command == 'ZADD':
            self.zsets.setdefault(key, {})[str(args[2])] = float(args[1])
            return 1
        if command == 'SET':
            if len(args) == 2:
                self.set(key, args[1])
            else:
                assert args[2] == 'EX'
                self.set(key, args[1], ex=int(args[3]))
            return 'OK'
        if command == 'DEL':
            count = 0
            for target in args:
                count += int(self.exists(target))
                self.hashes.pop(target, None); self.zsets.pop(target, None); self.strings.pop(target, None)
                if hasattr(self.backend, 'values'):self.backend.values.pop(target, None)
            return count
        raise AssertionError('Unexpected local Redis command: ' + command)

    def eval(self, source, key_count, *values):
        with self.lock:
            return self._eval(source, values[:key_count], values[key_count:])

    def _eval(self, source, keys, args):
        library = ctypes.util.find_library('lua5.4')
        if not library:
            raise RuntimeError('liblua5.4 is required for this offline script check')
        lib = ctypes.CDLL(library)
        pointer = ctypes.c_void_p
        lib.luaL_newstate.restype = pointer
        lib.luaL_openlibs.argtypes = [pointer]
        lib.lua_close.argtypes = [pointer]
        lib.lua_gettop.argtypes = [pointer]; lib.lua_gettop.restype = ctypes.c_int
        lib.lua_settop.argtypes = [pointer, ctypes.c_int]
        lib.lua_rawlen.argtypes = [pointer, ctypes.c_int]; lib.lua_rawlen.restype = ctypes.c_size_t
        lib.lua_rawgeti.argtypes = [pointer, ctypes.c_int, ctypes.c_longlong]
        lib.lua_type.argtypes = [pointer, ctypes.c_int]; lib.lua_type.restype = ctypes.c_int
        lib.lua_toboolean.argtypes = [pointer, ctypes.c_int]; lib.lua_toboolean.restype = ctypes.c_int
        lib.lua_tonumberx.argtypes = [pointer, ctypes.c_int, pointer]; lib.lua_tonumberx.restype = ctypes.c_double
        lib.lua_tolstring.argtypes = [pointer, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]; lib.lua_tolstring.restype = pointer
        lib.lua_pushnil.argtypes = [pointer]
        lib.lua_pushboolean.argtypes = [pointer, ctypes.c_int]
        lib.lua_pushinteger.argtypes = [pointer, ctypes.c_longlong]
        lib.lua_pushnumber.argtypes = [pointer, ctypes.c_double]
        lib.lua_pushlstring.argtypes = [pointer, ctypes.c_char_p, ctypes.c_size_t]
        lib.lua_createtable.argtypes = [pointer, ctypes.c_int, ctypes.c_int]
        lib.lua_rawseti.argtypes = [pointer, ctypes.c_int, ctypes.c_longlong]
        lib.lua_setglobal.argtypes = [pointer, ctypes.c_char_p]
        lib.luaL_loadstring.argtypes = [pointer, ctypes.c_char_p]; lib.luaL_loadstring.restype = ctypes.c_int
        lib.lua_pcallk.argtypes = [pointer, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_longlong, pointer]
        lib.lua_pcallk.restype = ctypes.c_int
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, pointer)
        lib.lua_pushcclosure.argtypes = [pointer, callback_type, ctypes.c_int]
        vm = lib.luaL_newstate()
        lib.luaL_openlibs(vm)

        def read(index):
            kind = lib.lua_type(vm, index)
            if kind == 0:
                return None
            if kind == 1:
                return bool(lib.lua_toboolean(vm, index))
            if kind == 3:
                number = lib.lua_tonumberx(vm, index, None)
                return int(number) if number.is_integer() else number
            if kind == 4:
                length = ctypes.c_size_t()
                data = lib.lua_tolstring(vm, index, ctypes.byref(length))
                return ctypes.string_at(data, length.value).decode()
            if kind == 5:
                absolute = index if index > 0 else lib.lua_gettop(vm) + index + 1
                values = []
                for slot in range(1, lib.lua_rawlen(vm, absolute) + 1):
                    lib.lua_rawgeti(vm, absolute, slot)
                    values.append(read(-1)); lib.lua_settop(vm, -2)
                return values
            raise AssertionError('Unsupported Lua result type: ' + str(kind))

        def push(value):
            if value is None:
                lib.lua_pushnil(vm)
            elif isinstance(value, bool):
                lib.lua_pushboolean(vm, value)
            elif isinstance(value, int):
                lib.lua_pushinteger(vm, value)
            elif isinstance(value, float):
                lib.lua_pushnumber(vm, value)
            elif isinstance(value, (tuple, list)):
                lib.lua_createtable(vm, len(value), 0)
                for index, item in enumerate(value, 1):
                    push(item); lib.lua_rawseti(vm, -2, index)
            else:
                data = str(value).encode()
                lib.lua_pushlstring(vm, data, len(data))

        @callback_type
        def call(_vm):
            try:
                values = [read(i) for i in range(1, lib.lua_gettop(vm) + 1)]
                result = self._command(*values)
                push(True); push(result)
            except Exception as exc:
                push(False); push(str(exc))
            return 2

        try:
            lib.lua_pushcclosure(vm, call, 0); lib.lua_setglobal(vm, b'_offline_call')
            push(list(keys)); lib.lua_setglobal(vm, b'KEYS')
            push([str(v) for v in args]); lib.lua_setglobal(vm, b'ARGV')
            wrapped = "local redis = {call=function(...) local ok,r=_offline_call(...); if not ok then error(r) end; return r end}\n" + source
            status = lib.luaL_loadstring(vm, wrapped.encode())
            if not status:
                status = lib.lua_pcallk(vm, 0, 1, 0, 0, None)
            if status:
                raise RuntimeError(str(read(-1)))
            return read(-1)
        finally:
            lib.lua_close(vm)
