import datetime
import logging


class ConnectionStateFilter(logging.Filter):
    # pylint: disable=too-few-public-methods

    rate_limit_timeouts = {}

    def __init__(self):
        logging.Filter.__init__(self)
        self.last_event = {}

    def filter(self, record):
        if "rate_limit_tag" in record.__dict__ and "rate_limit_timeout" in record.__dict__:
            tag = record.__dict__["rate_limit_tag"]
            if (
                tag not in self.rate_limit_timeouts
                or self.rate_limit_timeouts.get(tag) < datetime.datetime.now()
            ):
                self.rate_limit_timeouts[tag] = (
                    datetime.datetime.now() + record.__dict__["rate_limit_timeout"]
                )
            else:
                return False
        if "cn_id" in record.__dict__:
            cn_id = record.__dict__["cn_id"]
            if cn_id in self.last_event:
                if self.last_event[cn_id] == record.msg:
                    return False
            self.last_event[cn_id] = record.msg
        return True
