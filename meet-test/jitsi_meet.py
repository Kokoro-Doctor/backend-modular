import uuid

def generate_jitsi_link():
    room_name = "kokoro-" + str(uuid.uuid4())
    link = f"https://meet.jit.si/{room_name}"
    return link

meet_link = generate_jitsi_link()
print(meet_link)
