from multiprocessing import Process
import admin_bot, crm_bot, dashboard

if __name__ == "__main__":
    a = Process(target=admin_bot.main)
    c = Process(target=crm_bot.main)
    d = Process(target=dashboard.main)
    a.start()
    c.start()
    d.start()
    a.join()
    c.join()
    d.join()
