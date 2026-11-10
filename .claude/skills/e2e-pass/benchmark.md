# Benchmark of home's spend

Five tasks for the home instance, run in this order. Each prompt is sent as written, in a new thread on home, in Ask mode. How to run them and read them with the token probe is in `SKILL.md`, under Benchmark.

## 1. Memory report across instances

```text
Go through every instance on the hub and tell me what each one remembers: how many facts it holds, which ones it learned in the last seven days, and any that look stale or contradict each other. End with the instance whose memory most needs my attention, and why.
```

## 2. Instance with two routines

```text
Create an instance called Bench that helps me plan my reading. Give it two routines. weekly-plan runs every Monday at 08:00 and drafts the week's reading plan from what I finished the week before. sunday-checkin runs every Sunday at 18:00 and asks me how the week's reading went. Keep the defaults for everything else.
```

## 3. Skill from git

```text
Add the better-writing skill from github.com/jorgesolerrr/skills to the library. It lives in skills/jakubkrehel/better-writing. Then give it to the Bench instance.
```

## 4. Small factory

```text
Write a small factory called bench-brief with one role, briefer. A routine in the briefer template hands the factory a GitHub issue number when I run it by hand. One agent step reads that issue and writes a brief of at most five lines to a file in the instance's workspace. Make it pass the factory check, and don't create an instance from it.
```

## 5. Status report

```text
Give me a status report of the hub: what needs me first, then what is wrong, then how each instance is doing in a line or two.
```
