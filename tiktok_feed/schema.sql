-- Synthetic short-video feed database (MySQL 8, InnoDB, utf8mb4). All creators, users
-- and videos are fictional. No FOREIGN KEY constraints on purpose (see defect D8).
-- ID ranges are disjoint per entity: sounds 800+, creators 3000+, videos 5000000+,
-- users 100000+, impressions 1000000+, likes 2000000+, comments 3000000+,
-- follows 4000000+, reports 6000000+, promotions 600000+.

DROP TABLE IF EXISTS promotions;
DROP TABLE IF EXISTS reports;
DROP TABLE IF EXISTS follows;
DROP TABLE IF EXISTS comments;
DROP TABLE IF EXISTS likes;
DROP TABLE IF EXISTS impressions;
DROP TABLE IF EXISTS videos;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS creators;
DROP TABLE IF EXISTS sounds;

CREATE TABLE sounds (
  sound_id      INT PRIMARY KEY,
  title         VARCHAR(80) NOT NULL,
  duration_sec  INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE creators (
  creator_id      INT PRIMARY KEY,
  handle          VARCHAR(40) NOT NULL,
  country         CHAR(2) NOT NULL,
  signup_date     DATE NOT NULL,
  follower_count  INT NOT NULL COMMENT 'Cached number of followers'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
  user_id      INT PRIMARY KEY,
  handle       VARCHAR(40) NOT NULL,
  country      CHAR(2) NOT NULL,
  signup_date  DATE NOT NULL,
  device_id    VARCHAR(20) NOT NULL COMMENT 'Registering device'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE videos (
  video_id      INT PRIMARY KEY,
  creator_id    INT NOT NULL COMMENT 'creators.creator_id',
  sound_id      INT NOT NULL,
  posted_at     DATETIME NOT NULL COMMENT 'Local time',
  duration_sec  VARCHAR(10) NULL COMMENT 'Video length in seconds',
  category      VARCHAR(20) NOT NULL,
  status        VARCHAR(20) NOT NULL COMMENT 'PUBLISHED | PRIVATE | REMOVED',
  removed_at    DATETIME NULL,
  like_count    INT NOT NULL COMMENT 'Cached number of likes'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE impressions (
  impression_id  INT PRIMARY KEY,
  uid            INT NOT NULL COMMENT 'Viewer',
  video_id       INT NOT NULL,
  shown_at       DATETIME NOT NULL,
  source         VARCHAR(20) NOT NULL COMMENT 'CLIENT | SERVER',
  watch_ms       INT NOT NULL,
  completed      TINYINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE likes (
  like_id    INT PRIMARY KEY,
  viewer_id  INT NOT NULL COMMENT 'User who liked',
  video_id   INT NOT NULL,
  liked_at   DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE comments (
  comment_id    INT PRIMARY KEY,
  commenter_id  INT NOT NULL COMMENT 'User who commented',
  video_id      INT NOT NULL,
  created_at    DATETIME NOT NULL,
  text_len      INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE follows (
  follow_id    INT PRIMARY KEY,
  follower_id  INT NOT NULL COMMENT 'User who follows',
  creator_id   INT NOT NULL,
  followed_at  DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE reports (
  report_id    INT PRIMARY KEY,
  video_id     INT NOT NULL,
  reporter_id  INT NOT NULL,
  reason_code  TINYINT NOT NULL COMMENT '1=Spam, 2=Nudity, 3=Hate speech, 4=Misinformation',
  reported_on  DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE promotions (
  promo_id        INT PRIMARY KEY,
  video_id        INT NOT NULL,
  billing_system  VARCHAR(20) NOT NULL COMMENT 'ADS_V2 | LEGACY',
  status          VARCHAR(20) NOT NULL COMMENT 'active | paused | ended',
  budget          DECIMAL(14,2) NOT NULL COMMENT 'Promotion budget',
  start_date      DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
