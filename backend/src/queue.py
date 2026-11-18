import os
import redis
# Single Redis handle shared by the API routes and the background worker.
r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
ESTIMATE_QUEUE = 'mould-estimates'
ASSESSMENT_QUEUE = 'maintenance-assessments'
