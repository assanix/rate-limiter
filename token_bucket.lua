-- Token Bucket Algorithm implementation
-- KEYS[1]: Redis key for the client bucket
-- ARGV[1]: Current timestamp (seconds)
-- ARGV[2]: Bucket capacity (max tokens)
-- ARGV[3]: Refill rate (tokens per second)
-- ARGV[4]: Tokens to consume (usually 1)

local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_rate = tonumber(ARGV[3])
local tokens_to_consume = tonumber(ARGV[4])

-- Get current state or initialize
local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local current_tokens = tonumber(bucket[1]) -- Renamed for clarity
local last_refill = tonumber(bucket[2])

if current_tokens == nil then
    -- New bucket
    current_tokens = capacity
    last_refill = now
else
    -- Refill tokens based on time elapsed
    local elapsed = now - last_refill
    current_tokens = math.min(capacity, current_tokens + (elapsed * refill_rate))
    last_refill = now
end

-- Check if request can be allowed and calculate remaining AFTER potential consumption
local allowed = 0
local remaining -- Will be set below
local reset_time = 0 -- Time until next token available (only if denied)

if current_tokens >= tokens_to_consume then
    -- Allow request
    allowed = 1
    current_tokens = current_tokens - tokens_to_consume -- Consume tokens
    remaining = current_tokens -- Remaining is the value AFTER consumption
    reset_time = 0 -- No reset needed immediately
else
    -- Deny request
    allowed = 0
    remaining = 0 -- Standard practice to show 0 remaining when denied
    reset_time = math.max(0, (tokens_to_consume - current_tokens) / refill_rate) -- Calculate time until next token
end

-- Update bucket state in Redis (with the potentially consumed value)
redis.call('HMSET', key, 'tokens', current_tokens, 'last_refill', last_refill)

-- Set expiry only when state is updated, sliding window for cleanup
-- Consider a longer TTL if needed, or adjust based on activity
local ttl = math.max(3600, reset_time * 2) -- Expire after 1 hour OR double the time needed for reset, whichever is longer
redis.call('EXPIRE', key, math.ceil(ttl))

-- Return: allowed_status, remaining_tokens_after_action, seconds_until_next_token_if_denied
return {allowed, remaining, reset_time}