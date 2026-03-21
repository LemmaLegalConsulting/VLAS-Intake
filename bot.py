from intake_bot.bot import bot as intake_bot_bot
from pipecat.runner.run import main


async def bot(runner_args):
    return await intake_bot_bot(runner_args)


if __name__ == "__main__":
    main()
