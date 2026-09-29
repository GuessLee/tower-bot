# Manual slash-command checklist (staging guild)

Bots cannot invoke another bot's slash commands, so these are checked by a human.

- [ ] `/gamenight new` (empty) posts a card for next Wednesday 9:00 PM ET with ✅ ❔ ❌ 1️⃣-4️⃣
- [ ] `/gamenight new when:fri 8pm note:test` shows the note and Friday 8:00 PM
- [ ] `/gamenight new when:yesterday` replies ephemerally with the help text, posts nothing
- [ ] `/gamenight poll times:wed 9pm, thu 9pm` posts a poll with Wednesday first
- [ ] `/gamenight pick option:1` by the poster creates a night card; by someone else is refused
- [ ] `/gamenight move when:sat 8pm` edits the card, the event, and replies "Moved to"
- [ ] `/gamenight game number:2` sets the crown on game 2
- [ ] `/gamenight cancel` marks the card cancelled and removes the event
- [ ] `/games suggest name:Rocket Leage` offers "Did you mean Rocket League?" with buttons
- [ ] `/games list` is ephemeral; `/games remove` refused for non-admin
- [ ] `/feedback text:hello` returns an issue link (then close the issue)
- [ ] "Next up" and library pins are pinned and update after the above
