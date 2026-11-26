from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./giraffic.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)

    full_name = Column(String, nullable=False)
    username = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)

    password_hash = Column(String, nullable=False)
    grade = Column(Integer, nullable=False)
    birth_date = Column(Date, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, default=datetime.utcnow)
    last_activity_date = Column(Date, nullable=True)
    streak_days = Column(Integer, default=0)

    results = relationship("OlympiadResult", back_populates="user")
    course_visits = relationship("CourseVisit", back_populates="user")
    orders = relationship("Order", back_populates="user")


class OlympiadResult(Base):
    __tablename__ = "olympiad_results"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    filename = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    grade = Column(Integer, nullable=True)
    correct = Column(Integer, default=0)
    total = Column(Integer, default=0)
    completed_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="results")


class CourseVisit(Base):
    __tablename__ = "course_visits"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    filename = Column(String, nullable=False)
    title = Column(String, nullable=True)
    first_visited_at = Column(DateTime, default=datetime.utcnow)
    last_visited_at = Column(DateTime, default=datetime.utcnow)
    visits = Column(Integer, default=1)

    user = relationship("User", back_populates="course_visits")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    result_id = Column(Integer, ForeignKey("olympiad_results.id"), nullable=False, index=True)

    award_key = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    participant_name = Column(String, nullable=False)

    status = Column(String, default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="orders")


def init_db():
    Base.metadata.create_all(bind=engine)