# CS 457 Project Concurrency Design

**Student Name:** Andrew Barton  
**Date:** 2026-10-17    
**Course:** CS 457 - Computer Networks and the Internet

## Concurrency Design for Russian Roulette:

### Game Turn Logic and Design:

Inherently, the game's design splits control over what is currently happening between a player, their connection and the server. The game's state is only advanced consequent to a connection status change or a player's choice, with an intermediary step at the server, where the server modifies game state -- client connection states, player round count and the state of each player's reward cycle, revolver chamber state, state revision ID, turnID, dare status, game status and each players valid actions -- and transfers control of the game's state between players -- inherently acting as it's own mutex for controlling access to the game's shared resources. For this reason, I have chosen to go with non-blocking I/O multiplexing using Python's 'selectors'.

At the start of a game, the server waits for two incoming connections on a socket. Once a client connects, it is given it's own socket while the original socket waits for a second connection. 

After both of those connections are established, the first player to join is given control first, and then the game enters it's play state loop -- the player is given a choice between attempting a trigger pull, a pass, or quitting. 

Quitting is considered an immediate forfeit, connections are closed and the game resets. If a client is detected as having an interruption through a failed ping/pong heartbeat response, they are given thirty seconds to reconnect, during which if they reconnect gameplay continues as normal. If a move was sent prior to the interruption and it was received in full by the server, the server processes the move and when the player is reconnected their state is updated as though the move had been successful and the interruption hadn't happened at all. If the player experiences their interruption during or after sending their move command, the move command is rejected as bad data and the player resumes their turn from before the interruption. 

Failing those circumstances, the only way of advancing game state is through mutation of the server's state via 'action methods' passed to the server through turn choices that manipulate the game's state at the server after verification as valid moves for that player's state. 

This ensures that player actions are specifically *not* parallel by nature, and require no parallelism to operate the game's logic. In so doing, all game state actions -- both player and server -- can be handled without reasonably justifying threading. The game only ends when one player dies, and every move brings the game closer to that state recursively until the probability of a player's death is certain. The finite nature of this design strongly argues for the use of Python's selectors, due to the processing overhead on the server being minimal and client messages remaining small in size. 