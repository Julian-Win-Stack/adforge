# Two calls that shaped the agents

AdForge started as fixed steps in code. Now it's one main agent the user chats with, plus one agent per scene that makes and fixes its clip. Two big calls got it there. Both came from problems where patching just wasn't working anymore, and both cost me something.

## 1. Letting the agent decide, instead of coding every case

By late October the app was really a pipeline with about 180 checks in code. About 15 of them were dead ends: when one fired, nothing could move and nobody told the user. Most of the ads that failed died there. And the user couldn't steer it either. I asked it twice to show a dog's paws being cleaned instead of the presenter, and it refused both times because of a rule in code. Every new wish needed its own case coded first.

So I had to choose: keep coding every case a user might ask for, or let the agent decide. I let it decide.
- One main agent with general tools for the plan, the pictures and the scenes.
- Every check now tells the agent why instead of stopping everything. Only 3 hard refusals stay in code, and I add a new check only after I've seen the failure.
- Looking at pictures is a side call that answers in words, so the main agent's memory stays small.
- It acts on a note and then says what it cost. The user has a pause button.

It wasn't free. I deleted 18,601 lines I'd built in the weeks before, including a second agent that planned fixes. I also gave up asking the user for a yes before paid changes, and some predictability.

## 2. One agent per scene, instead of one agent for everything

Even after that, the first prompt for every product shot was written by a model that never saw a picture, just a label and one sentence. The photos it sent were often wrong, and the video model copied their backgrounds and random strangers. In my late-October test only 4 of 15 ads worked.

Creatify's agent is one agent that does everything, and I looked hard at switching to that. But one agent seeing every scene's pictures would flood its memory, and I don't want to copy something without a reason. So each scene gets its own agent. It picks its photos, draws the starting pic, gets it checked, films the clip and fixes it. The main agent passes down the user's exact words and only gets words back. The first 15 clips made this way all passed.

The cost is scene agents can't see each other, so scenes can drift, like a 2 oz bottle in one scene and the 8 oz in the next. A single agent like Creatify's keeps scenes consistent. For now I lock the product, its size and shared props in the plan. The full fix would be the main agent reviewing the whole ad at the end, but that's slow.

Code: the main agent is `backend/agents/producer.py`, the side call that looks is `backend/agents/look.py`, the hard refusals are `backend/agents/hard_list.py`, and the scene agent is `backend/agents/scene_agent/`. Decision records: [docs/adr/](../adr/README.md).
