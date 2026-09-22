from multiprocessing import Process
import admin_bot, crm_bot

if __name__ == "__main__":
    a = Process(target=admin_bot.main)
    c = Process(target=crm_bot.main)
    a.start()
    c.start()
    a.join()
    c.join()
