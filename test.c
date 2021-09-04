/*
 * Copyright 2021, Xcalar Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// Author: Eric D. Cohen

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>
#include <time.h>
#include <stdint.h>
#include <assert.h>

void allocLeak(void) {
    // Reflected in summary output and grdump.py leak tracking.
    const int alloc_sz = 8192;
    char *buf = malloc(alloc_sz);
    memset(buf, 'A', alloc_sz);
}

void useAfterFree(void) {
    int *ptr = malloc(sizeof(int));

    *ptr = 7;
    // fool optimizer
    printf("ptr: %d\n", *ptr);
    free(ptr);
    printf("ptr: %d\n", *ptr);
    *ptr = 8;
    printf("ptr: %d\n", *ptr);
}

void doubleFree(void) {
    int *ptr = malloc(sizeof(int));

    *ptr = 7;
    // fool optimizer
    printf("ptr: %d\n", *ptr);
    free(ptr);
    free(ptr);
}

void bufOverrun(void) {
    const int alloc_sz = 32;
    char *ptr = malloc(alloc_sz);

    ptr[0] = 2;
    ptr[alloc_sz+1] = 7;
    // fool optimizer
    printf("ptr: %d\n", *ptr);
    printf("ptr: %d\n", ptr[alloc_sz+1]);
}

void checkPoison(const char expected) {
    const int alloc_sz = 32;
    char *buf = malloc(alloc_sz);
    for (size_t idx = 0; idx < alloc_sz; idx++) {
        assert(buf[idx] == expected);
    }
    free(buf);
}

void usage(const char *myname) {
    printf(
        "Trivial tests of some guardrails features.\n"
        "These will often run fine without guardrails, and cause a segfault\n"
        "when run in guardrails.\n\n"
        "NOTE: guardrails works by causing a segfault/crash/core on error.\n"
        "Tests INTENDED TO CRASH under guardrails marked with (*).\n"
        "\nNATIVE:              %s <options>\n"
        "GUARDRAILS: grrun.sh %s <options>\n"
        "    -d          (*) Double free\n"
        "    -D          Debugger pause\n"
        "    -l          Leak memory\n"
        "    -o          (*) Buffer overrun\n"
        "    -p <val>    Verify poison (must match grargs.txt -p option)\n"
        "    -s          Succeed and exit\n"
        "    -u          (*) Use after free\n"
    , myname, myname);
}

int main(int argc, char **argv) {
    int c;
    const char *progName = argv[0];

    if (argc == 1) {
        usage(progName);
        return 1;
    }

    const char * const optstr = "Ddhlop:su";
    while ((c = getopt (argc, argv, optstr)) != -1) {
        if (c == 'D') {
            printf("\nDebugger pause (PID: %d), press <enter> to continue...\n", getpid());
            getchar();
        }
    }

    optind = 1;

    while ((c = getopt (argc, argv, optstr)) != -1) {
        switch(c) {
            case 'D':
                break;
            case 'l':
                allocLeak();
                return 0;
                break;
            case 'o':
                bufOverrun();
                break;
            case 'd':
                doubleFree();
                break;
            case 'p':
                checkPoison(atoi(optarg));
                return 0;
                break;
            case 's':
                return 0;
                break;
            case 'u':
                useAfterFree();
                break;
            case 'h':
            default:
                usage(progName);
                return 0;
        }
    }

    return 1;
}
