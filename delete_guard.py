"""Additional process-local Linux seccomp layer. Not a complete filesystem sandbox."""
import ctypes
import errno
import os
import platform
import sys

DENY=0x00050000|errno.EPERM
ALLOW=0x7fff0000
ARCH_X86_64=0xc000003e
BLOCKED={76:"truncate",77:"ftruncate",82:"rename",84:"rmdir",87:"unlink",
         263:"unlinkat",264:"renameat",285:"fallocate",316:"renameat2",
         425:"io_uring_setup",426:"io_uring_enter",427:"io_uring_register",
         437:"openat2",101:"ptrace",311:"process_vm_writev"}
def filter_instructions():
    # LD ABS; JEQ/JSET K; RET K. Reject alternate ABIs and x32 syscalls.
    code=[(0x20,0,0,4),(0x15,1,0,ARCH_X86_64),(0x06,0,0,DENY),
          (0x20,0,0,0),(0x45,0,1,0x40000000),(0x06,0,0,DENY)]
    for number in sorted(BLOCKED):
        code.extend([(0x15,0,1,number),(0x06,0,0,DENY)])
    # Reject O_TRUNC on open/openat, even if other open flags are present.
    for number,offset in [(2,24),(257,32)]:
        code.extend([(0x15,0,3,number),(0x20,0,0,offset),
                     (0x45,0,1,512),(0x06,0,0,DENY),(0x20,0,0,0)])
    code.append((0x06,0,0,ALLOW))
    return code

def enforce_no_delete():
    if sys.platform!="linux" or platform.machine().lower() not in ("x86_64","amd64"):
        raise RuntimeError("删除保护要求 Linux x86-64。未启用保护，执行端拒绝启动。")
    class Instruction(ctypes.Structure):
        _fields_=[("code",ctypes.c_ushort),("jt",ctypes.c_ubyte),("jf",ctypes.c_ubyte),("k",ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_=[("length",ctypes.c_ushort),("filter",ctypes.POINTER(Instruction))]
    instructions=filter_instructions()
    array=(Instruction*len(instructions))(*(Instruction(*x) for x in instructions))
    program=Program(len(instructions),array)
    libc=ctypes.CDLL(None,use_errno=True)
    libc.prctl.restype=ctypes.c_int
    # Install before creating any worker threads; descendants inherit this layer.
    if libc.prctl(38,1,0,0,0)!=0:
        raise RuntimeError("无法启用 no_new_privs；执行端拒绝启动："+os.strerror(ctypes.get_errno()))
    if libc.prctl(22,2,ctypes.byref(program),0,0)!=0:
        raise RuntimeError("无法启用删除保护；执行端拒绝启动："+os.strerror(ctypes.get_errno()))
    return {"active":True,"mode":"seccomp-no-delete","scope":"agent process and descendants",
            "blocks":["delete","rename","truncate"],"not_full_readonly_sandbox":True}
