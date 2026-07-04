
# Plan 002: Invoke command palette items from the prompt

Using ^p to access the command palette is unnecessary when we're already in a fundamentally
text-based medium.  We should leave it available, but also you can just run palette items from the
prompt.

When the `>` character is the first character in the prompt, the palette autocomplete-chooser list
should appear as a "pop-up" just above the prompt textbox, justified to the left side of the screen.
The "search" box prompt at the top of the default palette chooser modal is not necessary, just show the
items. (Because the prompt textbox _is_ the "Search box" in this mode.)

## Main behavior

The text in the prompt textbox, _without_ the leading `>` character, is used as the typeahead search
into palette options.

The user can use the up/down arrow keys to browse the palette and highlight a different option, just
as in the case when the palette modal is opened with ^p.

When the user presses `enter` and a palette option is selected, it's executed, just as if the row
was selected in the Textual-built-in palette chooser experience. 

At the same time, the prompt textbox is cleared and readied for a new prompt input.

If the user has input a sequence of characters that rules out any palette options (`>asd3434j2asdadkjfkjl34kj`)
and then presses `enter`, just submit it as a regular prompt and let the LLM figure out what to
do with it.

### History browsing

When the user presses enter to select the palette option, the input textbox contents are 
replaced with `>` followed by the full "standard" name of the palette option. e.g. `>cle` might
get replaced with `>Clear session`.

The selected text option *is* then retained in the session input history. e.g., activating a palette
choice and then pressing up-arrow to "bring previous item up in the textbox" will recall that same
palette choice into the textbox: `>Clear session`.

When up/down browsing thru history, do not immedpately resurface the palette chooser pop-up

### "standard" vs "displayed" palette option names

Some palette commands have been modified with a "Dynamic" name that includes information about the current
setting of the command. e.g. "set effort level (high)" is a displayed name whereas the "set effort level"
root is the standard name. We should only trap the "root name" / "standard name" for the command in
such cases, as modifying the setting by using the palette cmd would immediately make recall of that same
string (e.g. with `(High)` at the end) not find the setting in question.

_That said_, for palette options that represent toggles ("Enable Foo" / "Disable Foo") and
dynamically show one or the other, we should actually just record whatever it was the user enacted.
i.e. if the user selects `>Disable Foo`, then we write `>Disable Foo` into the command history.

## UI changes

* `^p` does remain a way to invoke the system palette search built inot Textual.
* We do _not_ show `^p palette` on the statusbar at the bottom of the screen any more.
  * Instead, when the prompt textbox is empty, we show `> palette`. 
  * When the user types anything other than `>`, we then hide that UI hint element.

## Getting it out of the way

* If the user presses ESC, dismiss the search box. Continue with just inputting text into the prompt
  as usual ("just plain text").
* If the user has typed a sequence of characters that rules out any palette options, and then types
  a space (`' '`) or tab (`'\t'`) or inserts a newline (`\n`) character (e.g., with ctrl+enter),
  dismiss the palette search and input text to the prompt as usual.

## Converting legacy commands

* The `/clear` command should now be `>clear`. There is already a palette option for clear. So this
  just means that palette cmd provider becomes the only mechanism by which this is activated; we don't
  hardcode a check for `prompt == '/clear'` on prompt submission anymore. 
* We also did some extra shenanigans on that provider in @session_commands.py so that `/clear` would
  work in the palette, too. This is now effectively backwards. The palette just needs to search for
  `clear`.
