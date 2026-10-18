import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from delete_guard import ALLOW,DENY,ARCH_X86_64,BLOCKED,filter_instructions

def evaluate(number,flags=0,arch=ARCH_X86_64):
    data={0:number,4:arch,24:flags,32:flags};a=0;pc=0;code=filter_instructions()
    for _ in range(500):
        op,jt,jf,k=code[pc]
        if op==0x20:a=data.get(k,0);pc+=1
        elif op==0x15:pc+=1+(jt if a==k else jf)
        elif op==0x45:pc+=1+(jt if a&k else jf)
        elif op==0x06:return k
        else:raise AssertionError("Unexpected opcode")
    raise AssertionError("Filter did not terminate")
class GuardTests(unittest.TestCase):
    def test_delete_and_rename_syscalls_denied(self):
        for number in BLOCKED:
            with self.subTest(number=number):self.assertEqual(evaluate(number),DENY)
    def test_hardlink_scan_network_and_journal_allowed(self):
        for number in (0,1,3,9,21,41,43,49,50,56,60,72,83,86,228,231,257,262,265):
            with self.subTest(number=number):self.assertEqual(evaluate(number),ALLOW)
    def test_truncating_open_denied_but_append_allowed(self):
        for number in (2,257):
            self.assertEqual(evaluate(number,512|64|1),DENY)
            self.assertEqual(evaluate(number,1024|64|1),ALLOW)
    def test_alternate_abi_denied(self):
        self.assertEqual(evaluate(1,arch=0x40000003),DENY)
        self.assertEqual(evaluate(0x40000000|1),DENY)
    @unittest.skipUnless(sys.platform=="linux","Kernel filter requires Linux")
    def test_real_kernel_in_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);original=p/"original";original.write_text("keep")
            code="""
from pathlib import Path
import os,sys
from delete_guard import enforce_no_delete
p=Path(sys.argv[1]);enforce_no_delete()
os.link(p/'original',p/'linked')
for action in (lambda:os.unlink(p/'original'),lambda:os.rename(p/'original',p/'renamed'),lambda:open(p/'original','w')):
    try:action()
    except PermissionError:pass
    else:raise AssertionError('Destructive operation allowed')
assert (p/'original').read_text()=='keep'
"""
            result=subprocess.run([sys.executable,"-c",code,directory],cwd=str(Path(__file__).parent),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(original.read_text(),"keep")
if __name__=="__main__":unittest.main()
