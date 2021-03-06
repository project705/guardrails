#!/usr/bin/python

#
# Copyright 2021, Xcalar Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# Author: Eric D. Cohen


import argparse
from collections import Counter
import csv
import subprocess as sp
import re
import sys
import os

argParser = argparse.ArgumentParser()

argParser.add_argument('-b', dest='binary', required=False,
                    help='Path to binary run under guard rails')
argParser.add_argument('-c', dest='sortct', required=False, action='store_true',
                    help='Sort by leak count instead total memory leaked')
argParser.add_argument('-f', dest='fin', required=True,
                    help='GuardRails leak dump CSV file')
argParser.add_argument('-t', dest='top', required=False, type=int, default=sys.maxsize,
                    help='Only show up to this many top contexts')
args = argParser.parse_args()


class grDump(object):
    def __init__(self):
        self.data = []
        self.leaks = []
        # XXX: Needs to be an array of shared lib bases...
        self.baseAddr = 0
        self.myDir = os.path.dirname(os.path.realpath(__file__))
        try:
            with open(self.myDir + '/blist.txt', 'r') as fh:
                self.blist = fh.read().splitlines()
        except IOError:
            self.blist = []

    def loadData(self):
        with open(args.fin) as fh:
            self.data = fh.read().splitlines()
        self.baseAddr = int(self.data.pop(0).split('-')[0], 16)
        print("Program base address {:#x}".format(self.baseAddr))

    def resolveSym(self, addr):
        addrProc = sp.Popen("addr2line -Cfse " + args.binary + " " + str(addr), shell=True, stdout=sp.PIPE)
        return filter(lambda x: x, addrProc.stdout.read().split('\n'))

    def resolveSyms(self, addrs):
        # addr2line is surprisingly slow; resolves about 6 backtraces/sec
        addrProc = sp.Popen("addr2line -Capfse " + args.binary + " " + str(addrs), shell=True, stdout=sp.PIPE)
        return addrProc.stdout.read()

    def parseLeaks(self):
        ctr = Counter(self.data)
        leakFreq = ctr.items()
        leakFreq.sort(key=lambda x: x[1], reverse=True)
        leakFreq = [str(x[1]) + "," + x[0] for x in leakFreq]

        csvReader = csv.reader(leakFreq, delimiter=',')
        skipped = 0
        while True:
            try:
                row = csvReader.next()
            except csv.Error:
                skipped += 1
                continue
            except StopIteration:
                break

            leak = filter(lambda x: x, row)
            count = int(leak[0])
            elmBytes = int(leak[1])
            totBytes = count * elmBytes
            self.leaks.append({'count': count, 'elmBytes': elmBytes, 'totBytes': totBytes, 'backtrace': leak[2:]})

        if skipped:
            # Error rows are likely due to either a known bug in libunwind or
            # GuardRail's current naughty use of SIGUSR2.  They are rare enough
            # it shouldn't really matter for leak tracking purposes...
            print("Skipped %d erroneous leak record" % skipped)


    def printLeaks(self):
        self.parseLeaks()

        totalBytesLeaked = 0
        totalLeakCount = 0

        numContexts = len(self.leaks)
        for leak in self.leaks:
            totalBytesLeaked += leak['totBytes']
            totalLeakCount += leak['count']

        print "Leaked total of {:,d} bytes across {:,d} leaks from {:,d} contexts"\
                .format(totalBytesLeaked, totalLeakCount, numContexts)
        if args.sortct:
            self.leaks.sort(key=lambda x: x['count'], reverse=True)
        else:
            self.leaks.sort(key=lambda x: x['totBytes'], reverse=True)

        context = 0
        outStr = ""
        for leak in self.leaks:
            leakStr = "================================ Context {:>6,d} / {:,d} ================================\n"\
                    .format(context, numContexts)
            leakStr += "Leaked {:,d} bytes across {:,d} allocations of {:,d} bytes each:\n"\
                    .format(leak['totBytes'], leak['count'], leak['elmBytes'])
            leakNum = 0

            if args.binary:
                # XXX: Need to pull in shared lib bases here.
                abs_addrs = [hex(int(x, 16) - self.baseAddr) for x in leak['backtrace']]
                syms = self.resolveSyms(' '.join(abs_addrs))
                skipLeak = False
                for sym in syms.split('\n'):
                    if not sym:
                        continue
                    shortSym = re.sub(r'\(.*?\)', r'', sym)

                    for b in self.blist:
                        if re.search(b, shortSym):
                            skipLeak = True

                    leakStr += "#{: <2} {}\n".format(leakNum, shortSym)
                    leakNum += 1

                if skipLeak:
                    continue
                else:
                    outStr += leakStr
            else:
                for addr in leak['backtrace']:
                    outStr += "#{: <2} {} (No symbols, see -b option)\n".format(leakNum, addr)
                    leakNum += 1

            context += 1
            if context >= args.top:
                break

        print outStr

dumper = grDump()

dumper.loadData()
dumper.printLeaks()
