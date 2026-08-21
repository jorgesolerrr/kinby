from agent.agent import kinby

thread = {"configurable": {"thread_id": "Jorge"}}

turns = [
    {"messages": [("user", "Do any files in this project mention Neo4j?")]},
    {"messages": [("user", "Which files did you already read?")]},
]

for task in turns:
    for step in kinby.stream(task, thread, stream_mode="values"):
        step["messages"][-1].pretty_print()
        if step.get("files_read"):
            print("files_read:", step["files_read"])

print("\n--- state history (thread Jorge) ---")
for snap in kinby.get_state_history(thread):
    cfg = snap.config["configurable"]
    print(
        f"step={snap.metadata.get('step')} "
        f"source={snap.metadata.get('source')} "
        f"next={snap.next} "
        f"checkpoint_id={cfg.get('checkpoint_id')} "
        f"messages={len(snap.values.get('messages', []))} "
        f"files_read={snap.values.get('files_read')}"
    )
#print(kinby.get_graph().draw_ascii())