from multiprocessing import Process
import admin_bot, crm_bot, dashboard, gvoice

if __name__ == "__main__":
    targets = [admin_bot.main, crm_bot.main, dashboard.main]
    if gvoice.configured():
        targets.append(gvoice.main)
    procs = [Process(target=t) for t in targets]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
