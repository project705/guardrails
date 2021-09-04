# Guardrails Memory Debugger

## Background

Guardrails was written years ago to debug a highly parallel distributed OLAP
database.  The database had a custom runtime that managed many user-mode fibers
with makecontext, swapcontext and getcontext.  Most of the
test/verification/automation infrastructure assumed clusters consisting of
many-core machines.  This resulted in very poor valgrind performance due to
thread serialization. Older ASAN implementations had difficulty dealing with
the swapcontext calls.  Thus guardrails was born.

These days you should probably start with tools like ASAN, valgrind, radare,
etc.  However the relative simplicity of the guardrails approach allows a lot
of flexibility in modifying the tool itself for specific debugging tasks. Note
that guardrails performance will be suboptimal under very heavy heap churn of
small allocations. In the case of the OLAP database, this was of minimal impact
because the heap was mostly used for the control path (very complex but
relatively lightly loaded) and not the data path, which was the performance
critical path but largely relied on larger allocations via a custom slab
allocator.

## Introduction

GuardRails is a MMU-based memory debugging tool.  The objective is to support
similar functionality to valgrind but with much higher performance in highly
threaded environments.  GuardRails is a full replacement for the libc memory
allocator.  Calls such as malloc, memalign, free, realloc, etc. are all handled
by GuardRails.  This is achieved using the LD_PRELOAD environment variable to
force the loader to overlay the GuradRails shared library over libc.

Valgrind is a great debugging tool but is far too slow to run massively
parallel tests, especially on many-core machines. The Valgrind VM alone running
memcheck exhibits a [50x performance degradation over
native](http://valgrind.org/docs/manual/manual-core.html#manual-core.whatdoes).
The problem is further compounded by the fact that Valgrind [serializes
threads](http://valgrind.org/docs/manual/manual-core.html#manual-core.pthreads),
limiting execution to a single core regardless of machine size.  These two
factors together can result in a <b>performance degradation factor of over
1,000x</b> for many-core machines.  Aside from the performance implications,
Valgrind fully virtualizes program execution, resulting in other undesired
side-effects related to shared memory, signal handling, etc.

## Guardrails tools
* `grrun.sh` runs executables under guardrails
* `test` a very simple test program exercising some guardrails functionality (see `./test -h`)
* `grgdb.py` GDB debugging macros
* `grdump.py` leak dumping utility
* `libguardrails.so.0.0` guardrails shared library; provides dumb best-fit
  logarithmic allocator along with tracking and protection mechanisms

# GuardRails Debugging Features

GuardRails uses the hardware MMU to detect memory errors by modifying the page
protection bits as memory is allocated and freed.  Before actually being
recycled freed memory is added to a delay list during which time the pages are
fully read/write protected.  This ensures that any use-after-free causes a
segmentation fault.  Heap header protection is achieved by appending a
protected guard page adjacent to all memory allocations.  Any corruption of
this header will cause a segfault indicating who did the corruption.

Current features include:

* Protection of the heap metadata.  This catches the infamous "__libc_message" errors from the libc heap allocator.
* Buffer overrun detection with allocation and free tracking.
* Context aware leak tracking with full allocator backtrace.  Both size and frequency of leaks are tracked and reported.
* Double free detection with allocation and free tracking.
* Use-after-free detection with allocation and free tracking.
* Memory poisoning and verification.
* Delayed free lists.
* Various OOM handling options.
* High-watermark tracking with dump intervals.
* Debugging of transient hidden runtime leaks (leaks that are freed via destructors on program exit).
* Various stats such as allocation histograms, rates, etc.

Future features:

* Better test infra
* Improved use-after-free performance.
* Buffer underrun detection.
* Thread-local memory
* Lockless pools

# Building Guardrails

A full build can be done with:

```console
make deps
make
```

This will result in a shared library `libguardrails.so.0.0` build product and a test program `test`.

# Running Under Guardrails

You'll may need to increase the `max_map_count` proc node to prevent allocation failures with more complex allocation patterns:

`echo 10000000 | sudo tee /proc/sys/vm/max_map_count`

and

`echo 0 | sudo tee /proc/sys/kernel/randomize_va_space`

Then use the wrapper script to run the program:

`grrun.sh <program>`

For example:

`grrun.sh ./test -o`

to run the simple buffer overrun test (expected to segfault).

No need to recompile the program; guardrails can run against any arbitrary binary.

# GuardRails Debugging Techniques

GuardRails provides several facilities for both detecting and also rapidly
fixing memory related bugs. In general, <b><u>when such bugs are detected the program
will immediately segfault</u></b>.  Debugging can then commence via an attached gdb
session or core file (limited functionality at the moment) using the supplied
GDB macros.

The following techniques are useful when running under GuardRails.

## Memory Debugging

In general GuardRails works by causing the process to segfault the moment
corruption occurs.  Examining the backtrace from the core will show exactly
what caused the corruption.  Guardrails also adds significant additional
context, including the original allocator and original free'er of the memory in
question.

### GDB Macros

Several macros are provided to facilitate GuardRails debugging.  These can be loaded into GDB as follows:

`(gdb) source grgdb.py`

See "help <macro>" for up-to-date help and examples.

Current macros include:

* <b>gr-dump-in-flight</b>: Dump allocator metadata. This is mostly for dumping
  in-flight allocations to a file for subsequent post-processing with
  grdump.py.  This is NOT recommended for interactive debugging.

* <b>gr-find-header</b>: Given an arbitrary address, find the GuardRails header
  without depending on the delay list.  The output of this command can be used
  as input to gr-print-addr-info.

* <b>gr-find-delay-list</b>: Finds an address on the delayed free list
  (requires -d option when running GuardRails).  Any address within an
  allocated range can be supplied here.  This is useful whenever a segfault
  occurs, as the faulting address is likely to be on this list, indicating a
  corruption, use-after-free or double free.

* <b>gr-heap-meta-corruption</b>: Try to determine if a faulting address would
  cause heap corruption. This command indicates if accessing a given address
  would cause corruption by determining if the access falls on a guard page.

* <b>gr-print-addr-info</b>: Prints information about an address.  This
  includes the GuardRails header along with the backtraces of the allocator of
  the memory and the freer of the memory.  In general this information should
  result in an almost immediate fix.  Note that the address MUST be either the
  original address returned by the allocator (eg malloc), or the GuardRails
  header address.  If the address is some other pointer, use gr-find-header to
  get the associated header address.

* <b>gr-print-segv</b>: Print segfault address.

#### GDB Macros Example Session

What follows is an example session debugging a corruption using GuardRails GDB macros.  It assumes GuardRails was run with the the following command line in `grargs.txt`:

`-dvt30 -T30 -p 175`

GDB session workflow (of course all the standard GDB debugging facilities are also available but omitted here for brevity):

```
Program terminated with signal SIGSEGV, Segmentation fault.
#0  0x00007fc6af5b4818 in json_decref (json=0x7fc6aa5f2fd0) at jansson.h:111
111         if(json && json->refcount != (size_t)-1 && --json->refcount == 0)
(gdb) source grgdb.py

# The actual address that caused the segfault
(gdb) gr-print-segv
$1 = (void *) 0x7fc6aa5f2fd8

# Find the GuardRails header associated with that address
(gdb) gr-find-header 0x7fc6aa5f2fd8
Found valid header at: 0x7fc6aa5f2000

# Dump the header and traces associated with that address
(gdb) gr-print-addr-info 0x7fc6aa5f2000
Address 0x7fc6aa5f2000 is a header
Address 0x7fc6aa5f2000 is free
Header:
{magic = 14800349170826394398, binNum = 12, slotNum = 0, usrData = 0x7fc6aa5f2fc0, usrDataSize = 64, next = 0x0, prev = 0x0, allocBt = 0x7fc6aa5f2038}

# Backtrace that allocated the memory:
================ Allocation Trace: ================
Line 325 of "GuardRails.c" starts at address 0x7fc6af7c0f35 <memalignInt+357>
Line 299 of "../src/lib/libutil/MemTrack.cpp" starts at address 0xa9363f <_memAlloc(unsigned long, char const*, char const*)+127>
Line 996 of "../src/lib/libutil/MemTrack.cpp" starts at address 0xa960d9 <memAllocJson(unsigned long)+25>
Line 341 of "value.c" starts at address 0x7fc6af5b91fb <json_array+11>
Line 770 of "load.c" starts at address 0x7fc6af5b6185 <parse_value+741>
Line 734 of "load.c" starts at address 0x7fc6af5b609a <parse_value+506>
Line 734 of "load.c" starts at address 0x7fc6af5b609a <parse_value+506>
Line 779 of "load.c" starts at address 0x7fc6af5b61be <parse_value+798>
Line 898 of "load.c" starts at address 0x7fc6af5b6426 <parse_json+70>
Line 959 of "load.c" starts at address 0x7fc6af5b65c3 <json_loads+227>
Line 499 of "../src/lib/libqueryparser/QueryParser.cpp" starts at address 0x7582b6 <QueryParser::jsonParse(char const*, Dag*)+102>
Line 654 of "../src/lib/libqueryparser/QueryParser.cpp" starts at address 0x758d00 <QueryParser::parse(char const*, Dag**, unsigned long*)+240>
Line 2794 of "../src/lib/libdag/Retina.cpp" starts at address 0x7f2f06 <DagLib::importRetina(ApiImportRetinaInput*, ApiOutput**, unsigned long*, bool)+1110>
Line 37 of "../src/lib/libapis/ApiHandlerImportRetina.cpp" starts at address 0x7d1a3c <ApiHandlerImportRetina::run(ApiOutput**, unsigned long*)+140>
Line 73 of "../src/lib/libapis/ApisRecvObject.cpp" starts at address 0x79630f <ApisRecvObject::run()+639>
Line 1113 of "../src/lib/libapis/ApisRecv.cpp" starts at address 0x795e4b <doWorkForApiImmediate(void*)+363>
Line 65 of "../src/lib/libruntime/DedicatedThread.cpp" starts at address 0x9b55d8 <DedicatedThread::threadEntryPoint()+120>
Line 55 of "../src/lib/libruntime/Thread.cpp" starts at address 0x9bc911 <Thread::threadEntryPointWrapper(void*)+113>
Line 312 of "pthread_create.c" starts at address 0x7fc6ae722173 <start_thread+179>
Line 113 of "../sysdeps/unix/sysv/linux/x86_64/clone.S" starts at address 0x7fc6acbdc03d <clone+109>

# Backtrace that freed the memory:
================ Free Trace: ================
Line 755 of "../src/lib/libutil/MemTrack.cpp" starts at address 0xa93e99 <memFree(void*)+313>
Line 1002 of "../src/lib/libutil/MemTrack.cpp" starts at address 0xa96139 <memFreeJson(void*)+25>
Line 240 of "../src/lib/libqueryparser/QpMap.cpp" starts at address 0x765c80 <QpMap::parseJson(json_t*, json_error_t*, WorkItem**)+992>
Line 567 of "../src/lib/libqueryparser/QueryParser.cpp" starts at address 0x758718 <QueryParser::jsonParse(char const*, Dag*)+1224>
Line 654 of "../src/lib/libqueryparser/QueryParser.cpp" starts at address 0x758d00 <QueryParser::parse(char const*, Dag**, unsigned long*)+240>
Line 2794 of "../src/lib/libdag/Retina.cpp" starts at address 0x7f2f06 <DagLib::importRetina(ApiImportRetinaInput*, ApiOutput**, unsigned long*, bool)+1110>
Line 37 of "../src/lib/libapis/ApiHandlerImportRetina.cpp" starts at address 0x7d1a3c <ApiHandlerImportRetina::run(ApiOutput**, unsigned long*)+140>
Line 73 of "../src/lib/libapis/ApisRecvObject.cpp" starts at address 0x79630f <ApisRecvObject::run()+639>
Line 1113 of "../src/lib/libapis/ApisRecv.cpp" starts at address 0x795e4b <doWorkForApiImmediate(void*)+363>
Line 65 of "../src/lib/libruntime/DedicatedThread.cpp" starts at address 0x9b55d8 <DedicatedThread::threadEntryPoint()+120>
Line 55 of "../src/lib/libruntime/Thread.cpp" starts at address 0x9bc911 <Thread::threadEntryPointWrapper(void*)+113>
Line 312 of "pthread_create.c" starts at address 0x7fc6ae722173 <start_thread+179>
Line 113 of "../sysdeps/unix/sysv/linux/x86_64/clone.S" starts at address 0x7fc6acbdc03d <clone+109>

# Backtrace of the code that tried to use the memory after it was freed:
(gdb) bt
#0  0x00007fc6af5b4818 in json_decref (json=0x7fc6aa5f2fd0) at jansson.h:111
#1  hashtable_do_clear (hashtable=hashtable@entry=0x7fc6aa604fc0) at hashtable.c:148
#2  0x00007fc6af5b48d9 in hashtable_close (hashtable=0x7fc6aa604fc0) at hashtable.c:215
#3  0x00007fc6af5b97a1 in json_delete_object (object=0x7fc6aa604fb0) at value.c:77
#4  json_delete (json=0x7fc6aa604fb0) at value.c:936
#5  0x00007fc6af5b4834 in json_decref (json=<optimized out>) at jansson.h:112
#6  hashtable_do_clear (hashtable=hashtable@entry=0x7fc6aa60efc0) at hashtable.c:148
#7  0x00007fc6af5b48d9 in hashtable_close (hashtable=0x7fc6aa60efc0) at hashtable.c:215
#8  0x00007fc6af5b97a1 in json_delete_object (object=0x7fc6aa60efb0) at value.c:77
#9  json_delete (json=0x7fc6aa60efb0) at value.c:936
#10 0x00007fc6af5b9815 in json_decref (json=<optimized out>) at jansson.h:112
#11 json_delete_array (array=<optimized out>) at value.c:364
#12 json_delete (json=0x7fc6aa54efd0) at value.c:939
#13 0x0000000000758b5d in json_decref (json=0x7fc6aa54efd0) at /usr/include/jansson.h:112
#14 QueryParser::jsonParse (this=0x7fc66732b790, query=0x7fc67696ea10 "[{\"op"..., queryGraph=0x7fc491655a80) at ../src/lib/libqueryparser/QueryParser.cpp:619
#15 0x0000000000758d11 in QueryParser::parse (this=0x7fc66732b790, query=0x7fc67696ea10 "[{\"op"..., queryGraphOut=0x7fc476ca95b8, numQueryGraphNodesOut=0x7fc476ca95b0) at ../src/lib/libqueryparser/QueryParser.cpp:654
#16 0x00000000007f2f26 in DagLib::importRetina (this=0x7fc66a42af60, importRetinaInput=0x7fc6a9a28328, importRetinaOutputOut=0x7fc491651030, importRetinaOutputSizeOut=0x7fc491651038, persist=true) at ../src/lib/libdag/Retina.cpp:2794
#17 0x00000000007d1a44 in ApiHandlerImportRetina::run (this=0x7fc6a9a1ef80, output=0x7fc491651030, outputSize=0x7fc491651038) at ../src/lib/libapis/ApiHandlerImportRetina.cpp:37
#18 0x0000000000796345 in ApisRecvObject::run (this=0x7fc491651000) at ../src/lib/libapis/ApisRecvObject.cpp:73
#19 0x0000000000795e4b in apisRecvObjectImmediateHandler (apisRecvObject=0x7fc491651000) at ../src/lib/libapis/ApisRecv.cpp:1112
#20 doWorkForApiImmediate (args=0x4) at ../src/lib/libapis/ApisRecv.cpp:1138
#21 0x00000000009b55ec in DedicatedThread::threadEntryPoint (this=0x7fc67382edd8) at ../src/lib/libruntime/DedicatedThread.cpp:65
#22 0x00000000009bc91e in Thread::threadEntryPointWrapper (arg=0x7fc67382edd8) at ../src/lib/libruntime/Thread.cpp:55
#23 0x00007fc6ae722184 in start_thread (arg=0x7fc476cb0700) at pthread_create.c:312
#24 0x00007fc6acbdc03d in clone () at ../sysdeps/unix/sysv/linux/x86_64/clone.S:111
```

## SIGUSR2

Issuing SIGUSR2 to &lt;program_name&gt; running under GuardRails:

`pkill -SIGUSR2 <program_name>`

will cause the current GuardRails state to be dumped to stdout and also cause
GuardRails to write out the grtrack-&lt;tid&gt;.txt leak tracking file
(assuming the -t and/or -T option was passed).  Included in the state is the
arguments from grargs.txt, which can be used to verify that GuardRails is
running as intended.  If the -v option was passed, a per-memory-bin stats will
also be dumped.

Note that depending on the allocator state, it can take several minutes to dump
the tracker file.  To determine when it's done, you can do something like

`watch -n1 ls -al grtracker-*.txt`

to see when it stops growing.  lsof can also be used.  Further, the file is
also done dumping when the allocator state summary is finished dumping to
node.out (can tail -f this).

Note that grdump.py can also handle partial dumps, which might be sufficient
depending on the circumstances.

## Leak Tracking

Leak tracking must be enabled by adding the "-t <frames>" and/or "-T <frames>"
option to the grargs.txt file.  There are some deep stacks in the protobuf code
paths.  In general a max frame count of 30 is sufficient to get enough context
in all such cases.

When tracking leaks, GuardRails will dump an output CSV file called
"grtrack.txt" to the current working directory.  This file is dumped on exit
and also on SIGUSR2.  It can be post-processed by grdump.py.  This script
groups and counts all the leak contexts, generates a symbolic backtrace for
each context, and sorts them by either the total number of bytes leaked or by
the frequency of the leak.

If instead SIGUSR2 is used to dump grtrack.txt, it will contain not only leaked
memory but also any in-use memory at the time SIGUSR2 was issued.

### Leak Tracking Examples

To show the top 100 leaks by frequency:

<pre>
./grdump.py -f grtrack-&lt;tid&gt;.txt -b &lt;program_name&gt; -c -t 100 |less
Leaked total of 4,222,380 bytes across 62,116 leaks from 1,639 contexts
================================ Context      0 / 1,639 ================================
Leaked 171,008 bytes across 5,344 allocations of 32 bytes each:
#0  0x00007f457e430de9: ?? ??:0
#1  0x000000000091fe24: string_create at value.c:655
#2  0x000000000091ce60: parse_value at load.c:830
#3  0x000000000091cf6a: parse_object at load.c:734
#4  0x000000000091cf6a: parse_object at load.c:734
#5  0x000000000091d08e: parse_array at load.c:779
#6  0x000000000091d2f6: parse_json at load.c:898
#7  0x000000000091d46b: json_loads at load.c:959
#8  0x000000000069996f: QueryParser::jsonParse at QueryParser.cpp:499
#9  0x0000000000699de8: QueryParser::parse at QueryParser.cpp:648
#10 0x00000000006f12cb: DagLib::copyRetinaToNewDag at Retina.cpp:5947
#11 0x00000000006e02ea: Dag::copyDagToNewDag at Dag.cpp:2105
#12 0x00000000006e16f3: Dag::cloneDagLocal at Dag.cpp:2800
#13 0x00000000006e291a: Dag::cloneDagGlobalInt [256], unsigned long, char  [256], Dag::CloneFlags) at Dag.cpp:3676
#14 0x00000000006e2551: Dag::cloneDagGlobal [256], unsigned int, char  [256], Dag::CloneFlags) at Dag.cpp:3488
#15 0x00000000006ec54b: DagLib::makeRetina at Retina.cpp:2980
#16 0x00000000006d453b: ApiHandlerMakeRetina::run at ApiHandlerMakeRetina.cpp:50
#17 0x000000000075b569: processWorkItem at OperatorsFuncTest.cpp:413
#18 0x000000000076128e: workerRetina at OperatorsFuncTest.cpp:4359
#19 0x000000000075e6b2: workerMain at OperatorsFuncTest.cpp:4898
#20 0x00000000007bd835: DedicatedThread::threadEntryPoint at DedicatedThread.cpp:65
#21 0x00000000007bfe0f: Thread::threadEntryPointWrapper at ??:?
#22 0x00007f457d598e25: ?? ??:0
#23 0x00007f457bc5f34d: ?? ??:0
================================ Context      1 / 1,639 ================================
Leaked 84,096 bytes across 2,628 allocations of 32 bytes each:

remaining top 99 leaks elided...
</pre>

To show the top 100 by total leaked memory:

<pre>
./grdump.py -f grtrack-&lt;tid&gt;.txt -b &lt;program_name&gt; -t 100 |less
Leaked total of 4,222,380 bytes across 62,116 leaks from 1,639 contexts
================================ Context      0 / 1,639 ================================
Leaked 902,472 bytes across 1,213 allocations of 744 bytes each:
#0  0x00007f457e430de9: ?? ??:0
#1  0x00007f457e18524d: ?? ??:0
#2  0x000000000078b7c7: resultSetMakeLocal at LocalResultSet.cpp:147
#3  0x000000000078b401: usrNodeMsg2pcMakeResultSet at UsrNode.cpp:369
#4  0x00000000006910fa: MsgMgr::doTwoPcWorkForMsg at Message.cpp:3387
#5  0x00000000007bfcf5: SchedObject::getSchedulable at SchedObject.h:49
#6  0x00000000007bd551: memBarrier at System.h:65
#7  0x00007f457bbadd40: ?? ??:0
================================ Context      1 / 1,639 ================================
Leaked 171,008 bytes across 5,344 allocations of 32 bytes each:

remaining top 99 leaks elided...
</pre>

### Command Line Options

Because GuardRails is loaded before the loader parses the command line options
a configuration file must be used.  This file contains a single line of options
in standard getopt format.  Currently the following options are supported:

* ```-a```: Abort on rlimit-induced OOM rather than return NULL.
* ```-A```: Abort on actual OOM rather than return NULL.
* ```-D <hwm_bytes>```: Dump state every time memory high water mark increases
  by this interval or greater.
* ```-d```: Use delayed free list.  This provides very robust user-after-free
  detection/tracking but currently slows things down by a factor of about 8x.
  I have a fix planned to make this feature far more performant.
* ```-m <pct_mem>```: Limit allocation to pct_mem of total system memory.
* ```-M <max_request_bytes>```: Abort if a request exceeds max_request_bytes.
* ```-p <poison_byte>```: Prepoison allocated memory with poison_byte.
* ```-s <num>```: Number of slots to use.  This controls the level of
  parallelism in GuardRails.
* ```-t <frames>```: Enable leak detection and <b>allocation</b> tracking.  The
  argument specifies the maximum number of stack frames to track (30 seems to
  capture sufficient context in all paths).  Has some impact on performance but
  works with the operators test.  The memory overhead for this feature is
  modest because it usually uses memory that would otherwise be wasted by the
  allocator.
* ```-T <frames>```: Enable leak detection and <b>free</b> tracking.  Works
  like the -t option but for frees.
* ```-v```: Verbose mode.  Dumps size-based stats on exit or SIGUSR2.

These commands must go in a file named `grargs.txt` in the process's working
directory.

`sudo pwdx $(pgrep <program_name>)`

Note that GuardRails will display its argument string to stdout upon exit or
upon SIGUSR2.  For example:

```
 ================ BEGIN GUARDRAILS OUTPUT ================
 Ran with args: -v -t 30 -T 30 -p 175
 Number mem pools used: 2
 ...
```

This can be used to verify that the grargs.txt is being parsed as expected.

# Performance

Performance was measured for two workloads: one with lots of small heap allocations and another where control-path allocations are heap based and data-path allocations are slab-based.

| Performance Degredation Factor vs Native (slab-intensive workload) |       |
|--------------------------------------------------------------------|-------|
| Heap Corruption Tracking                                           | 1.00x |
| Memory Tracking                                                    | 1.10x |
| Protected Delay List                                               | 1.13x |

| Performance Degredation Factor vs Native (heap-intensive workload) |       |
|--------------------------------------------------------------------|-------|
| Heap Corruption Tracking                                           | 1.25x |
| Memory Tracking                                                    | 4.82x |
| Protected Delay List                                               | 8.21x |

# Troubleshooting

## Machine hangs, program runs very slow, never completes, etc

This is probably due to a high-rate leak of small allocations.  Because of
allocator overheads, GuardRails is inefficient for small allocations.  When
many such allocations leak, it can cause the machine to start swapping
extensively and bring things to a halt.  Swap activity can be viewed in vmstat
as follows:

 vmstat -w 1

pay attention to the si (swap in) and so (swap out) columns.  They should be at
or near zero.  If not there is non-trivial swapping.

To track down the culprit, issue the following command:

 pkill -SIGUSR2 &lt;program_name&gt;

which will cause GuardRails to dump the current tracker state to the
grtrack-*.txt file.  Although this includes legitimate in-flight allocations,
it will usually be very obvious where the leak is as you'll see millions of
tiny allocations at the very top of the leak count list.  This is probably the
problem.

## Missing Symbols in Leak Backtraces

Back in the day we did a lot of static linking for various reasons, and the
program loader seemed to use consistent offsets.  This is no longer the case.
I did a quick update to the leak dumper to pull in the program text base from
/proc/&lt;pid&gt;/maps but to get all the symbols lookups working we need to
pull in the base addresses of all the shared libraries from maps.  Should be
pretty easy to do based on the way the program text base address is pulled in.
Then one can just blindly iterate over these bases during symbol lookup and
stop when a lookup succeeds. Not perfect but should be good enough...
